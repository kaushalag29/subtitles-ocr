#!/usr/bin/env python3

import os
import sys
import srt
import google.generativeai as genai
import re
from datetime import timedelta
from gemini_rate_limiter import get_global_limiter

SRT_REFINEMENT_PROMPT = """You are a master SRT subtitle editor with a deep understanding of dialogue pacing for voice synthesis. Your goal is to refine the given SRT content to make it optimal for TTS and voice cloning, while strictly preserving the original timings.
The input is standard SRT format. You MUST return the output in the exact same SRT format.

Input SRT:
```srt
{srt_content_placeholder}
```

---
**Core Mandate: DO NOT CHANGE TIMESTAMPS**

*   **The start and end times for every subtitle MUST remain IDENTICAL to the input.** There are no exceptions. This is the most important rule.
*   All edits must be to the subtitle TEXT ONLY.

---
**Editing Rules:**

**Rule 1: Analyze Full Context First**

*   Before making any edits, read the entire subtitle file to understand the conversational flow, speaker intent, and emotional context. This understanding should guide all your decisions.

**Rule 2: Merge Consecutive Subtitles**

*   **A) Identical Text:** If two or more consecutive subtitles have the *exact* same text, merge them. The new entry uses the start time of the first and the end time of the last subtitle in the sequence.
*   **B) Continuous Sentences:** If two or more *different* consecutive subtitles clearly form a single continuous sentence from the same speaker, merge them. The new entry uses the start time of the first and end time of the last. Avoid merging if it feels unnatural.
*   **Note on Merging:** Merging is an exception to the "DO NOT CHANGE TIMESTAMPS" rule, but ONLY in the sense that the merged subtitle adopts the start/end times of the subtitles it replaces. You are not creating new, arbitrary timestamps.

**Rule 3: Adjust Text for Speakability (The ONLY Allowed Text Change)**

*   **A) If Text is TOO LONG for its Duration:**
    *   Subtly condense or rephrase the text to fit within the UNCHANGED duration.
    *   You MUST preserve the core meaning and emotional tone. Do not remove essential information. This is your ONLY option. You cannot extend the duration.
*   **B) If Text is TOO SHORT for its Duration:**
    *   **DO NOTHING.** Do not change the text or the timing. A short phrase with a long duration is often intentional for dramatic pacing.

**Rule 4: Overlaps**

*   The input subtitles may contain overlaps. Since you cannot change the timestamps, you MUST NOT attempt to fix them by adjusting times. If overlaps exist in the input, they will exist in the output. The only way an overlap should be removed is if the overlapping subtitles are merged under Rule 2.

**Rule 5: Final SRT Integrity**

*   After all edits, re-number all subtitles sequentially starting from 1.
*   Ensure all timecodes are in the correct `HH:MM:SS,ms` format and are identical to the input unless affected by a merge.
*   Ensure a blank line separates each subtitle entry.

---
Your output should be ONLY the refined SRT content, with no extra explanations.
"""

# Speech rate constants for speakability analysis
AVERAGE_WORDS_PER_MINUTE = 150  # Average speaking rate
SLOW_WORDS_PER_MINUTE = 120     # Slow speaking rate for complex content
FAST_WORDS_PER_MINUTE = 180     # Fast speaking rate for simple content

def calculate_speaking_duration(text):
    """Calculate estimated speaking duration for given text."""
    words = len(text.split())
    if words == 0:
        return 0.0
    
    # Adjust based on text complexity
    if any(char in text for char in '.,!?;:'):
        # Text with punctuation needs more time for natural speech
        wpm = SLOW_WORDS_PER_MINUTE
    else:
        wpm = AVERAGE_WORDS_PER_MINUTE
    
    return (words / wpm) * 60  # Convert to seconds

def analyze_speakability(subtitle):
    """Analyze if subtitle text can be comfortably spoken within its duration."""
    duration = (subtitle.end - subtitle.start).total_seconds()
    text = subtitle.content.strip()
    
    if not text:
        return True, "empty", 0
    
    estimated_duration = calculate_speaking_duration(text)
    
    # Allow 10% buffer for natural speech rhythm
    if estimated_duration <= duration * 1.1:
        return True, "comfortable", duration - estimated_duration
    else:
        return False, "too_fast", estimated_duration - duration

def intelligent_subtitle_analysis(subtitles):
    """
    Analyze each subtitle for context and speakability.
    Modify only when necessary based on duration constraints.
    """
    print("Performing intelligent subtitle analysis...")
    
    analyzed_subtitles = []
    modifications_made = 0
    
    for i, subtitle in enumerate(subtitles):
        is_speakable, status, time_diff = analyze_speakability(subtitle)
        
        if not is_speakable and abs(time_diff) > 0.5:  # Only modify if significantly problematic
            print(f"Subtitle {subtitle.index}: Text too long by {time_diff:.1f}s, analyzing for optimization...")
            
            # Since we cannot extend duration, we go straight to text condensation
            condensed_subtitle = try_condense_text(subtitle, time_diff)
            if condensed_subtitle and condensed_subtitle.content != subtitle.content:
                analyzed_subtitles.append(condensed_subtitle)
                modifications_made += 1
                print(f"  → Condensed text for subtitle {subtitle.index}")
            else:
                # Keep original if condensation wasn't effective or possible
                analyzed_subtitles.append(subtitle)
                print(f"  → No safe text condensation possible for subtitle {subtitle.index}")
        else:
            # Subtitle is already speakable or only slightly problematic
            analyzed_subtitles.append(subtitle)
    
    print(f"Intelligent analysis complete. Modified {modifications_made} subtitles.")
    return analyzed_subtitles

def try_extend_duration(subtitle, all_subtitles, current_index, needed_time):
    """Try to extend subtitle duration without causing overlaps."""
    # Maximum extension allowed (1 second each direction as per original prompt)
    max_extension = min(1.0, needed_time * 0.5)  # Be conservative
    
    # Check if we can extend start time (move it earlier)
    new_start = subtitle.start - timedelta(seconds=max_extension)
    prev_subtitle = all_subtitles[current_index - 1] if current_index > 0 else None
    
    if prev_subtitle and new_start <= prev_subtitle.end:
        # Can't extend start, try extending end only
        new_start = subtitle.start
        max_extension *= 2  # Use full extension on end
    
    # Check if we can extend end time (move it later)
    new_end = subtitle.end + timedelta(seconds=max_extension)
    next_subtitle = all_subtitles[current_index + 1] if current_index < len(all_subtitles) - 1 else None
    
    if next_subtitle and new_end >= next_subtitle.start:
        # Can't extend end, calculate what we can do
        available_end_extension = (next_subtitle.start - subtitle.end).total_seconds()
        if available_end_extension > 0.1:  # At least 100ms gap
            new_end = subtitle.end + timedelta(seconds=available_end_extension - 0.1)
        else:
            new_end = subtitle.end
    
    # Check if the extension provides enough improvement
    new_duration = (new_end - new_start).total_seconds()
    original_duration = (subtitle.end - subtitle.start).total_seconds()
    
    if new_duration > original_duration + 0.3:  # At least 300ms improvement
        new_subtitle = srt.Subtitle(
            index=subtitle.index,
            start=new_start,
            end=new_end,
            content=subtitle.content
        )
        return new_subtitle
    
    return None

def try_condense_text(subtitle, excess_time):
    """Try to condense subtitle text using AI while preserving meaning."""
    if excess_time < 1.0:  # Only condense if significantly over time
        return subtitle
    
    try:
        genai.configure(api_key=os.environ.get('GOOGLE_API_KEY'))
        safe = [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_NONE",
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH",
                "threshold": "BLOCK_NONE",
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_NONE",
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_NONE",
            },
        ]
        model = genai.GenerativeModel('gemini-2.5-flash-lite-preview-06-17', safety_settings=safe)
        limiter = get_global_limiter()
        
        condensation_prompt = f"""
You are an expert subtitle editor. Your task is to condense the following subtitle text to make it speakable in less time while preserving the core meaning and natural flow.

Original text: "{subtitle.content}"
Current duration: {(subtitle.end - subtitle.start).total_seconds():.1f} seconds
Estimated speaking time needed: {calculate_speaking_duration(subtitle.content):.1f} seconds
Excess time: {excess_time:.1f} seconds

Rules:
1. Preserve the core meaning and context.
2. Maintain natural grammar and readability.
3. Remove unnecessary words, not essential information.
4. Keep the condensed version natural for speech synthesis.
5. If the text cannot be meaningfully condensed without losing important information, return the original text unchanged.
6. Be conservative. Since you are editing this line in isolation, if you are unsure about the full context, it is safer to return the original text.

Return ONLY the condensed text (or original if no good condensation is possible), no additional formatting or explanation.
"""
        
        response = limiter.generate_content(model, condensation_prompt, 'gemini-2.5-flash-lite-preview-06-17')
        condensed_text = response.text.strip()
        
        # Validate the condensation
        if (len(condensed_text) < len(subtitle.content) and 
            len(condensed_text) > len(subtitle.content) * 0.5 and  # Not too aggressive
            calculate_speaking_duration(condensed_text) < calculate_speaking_duration(subtitle.content)):
            
            new_subtitle = srt.Subtitle(
                index=subtitle.index,
                start=subtitle.start,
                end=subtitle.end,
                content=condensed_text
            )
            return new_subtitle
    
    except Exception as e:
        print(f"Error in text condensation: {e}")
    
    return subtitle

def get_refined_srt_content(srt_content_str):
    genai.configure(api_key=os.environ.get('GOOGLE_API_KEY'))
    safe = [
        {
            "category": "HARM_CATEGORY_HARASSMENT",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_NONE",
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_NONE",
        },
    ]
    model = genai.GenerativeModel('gemini-2.5-flash-lite-preview-06-17', safety_settings=safe) # Using 2.0 Flash as it's good with long contexts
    limiter = get_global_limiter()

    prompt = SRT_REFINEMENT_PROMPT.replace("{srt_content_placeholder}", srt_content_str)
    
    print("Sending SRT content to GenAI for refinement...")
    response = limiter.generate_content(model, prompt, 'gemini-2.5-flash-lite-preview-06-17')
    
    # Extract content between ```srt and ```
    match = re.search(r"```srt\s*(.*?)\s*```", response.text, re.DOTALL)
    if match:
        refined_srt_text = match.group(1).strip()
        print("GenAI refinement complete.")
        return refined_srt_text
    else:
        # Fallback if ```srt ... ``` is not found, try to get content after "Output SRT:" or similar,
        # or just use the whole text if it looks like SRT. This part might need more robust parsing
        # depending on typical AI response structure if it deviates.
        print("Warning: ```srt block not found in AI response. Attempting to use full response.")
        # A simple check: if it contains '-->' it's likely SRT-ish
        if "-->" in response.text:
             # Attempt to clean up potential preamble if any, this is heuristic
            lines = response.text.splitlines()
            srt_lines = []
            found_first_subtitle_number = False
            for line in lines:
                if re.match(r"^\\d+$", line.strip()): # Starts with a number (subtitle index)
                    found_first_subtitle_number = True
                if found_first_subtitle_number:
                    srt_lines.append(line)
            if srt_lines:
                return "\\n".join(srt_lines)
            else: # If no numbered lines found, return raw text and hope for the best
                 return response.text 
        else: # If no '-->' it's probably not SRT
            print("Error: AI response does not appear to be valid SRT content.")
            print("AI Response was:\n", response.text)
            raise ValueError("AI response could not be parsed as SRT content.")

def main():
    if len(sys.argv) not in [3, 4]:
        print("Usage: python refine_srt.py <input_srt_file> <output_srt_file> [--skip-analysis]")
        print("  --skip-analysis: Skip the intelligent subtitle analysis step")
        sys.exit(1)

    input_srt_file = sys.argv[1]
    output_srt_file = sys.argv[2]
    skip_analysis = len(sys.argv) == 4 and sys.argv[3] == "--skip-analysis"

    if not os.path.exists(input_srt_file):
        print(f"Error: Input file '{input_srt_file}' not found.")
        sys.exit(1)

    try:
        with open(input_srt_file, 'r', encoding='utf-8') as f:
            original_srt_content = f.read()
    except Exception as e:
        print(f"Error reading input file '{input_srt_file}': {e}")
        sys.exit(1)

    if not original_srt_content.strip():
        print(f"Warning: Input file '{input_srt_file}' is empty. Writing empty output.")
        with open(output_srt_file, 'w', encoding='utf-8') as f:
            f.write("")
        sys.exit(0)

    try:
        # Test if input is valid SRT
        parsed_subtitles = list(srt.parse(original_srt_content))
    except Exception as e:
        print(f"Error: Input file '{input_srt_file}' is not valid SRT or cannot be parsed by srt library: {e}")
        print("Consider checking the output of the previous step (e.g., srt-normalise).")
        # Copy original to output if it's invalid, to not break the pipeline
        try:
            with open(output_srt_file, 'w', encoding='utf-8') as f_out:
                f_out.write(original_srt_content)
            print(f"Copied original (invalid) SRT to '{output_srt_file}' due to parsing error.")
        except Exception as e_write:
            print(f"Error writing to output file '{output_srt_file}': {e_write}")
        sys.exit(1)

    try:
        # Step 1: Intelligent subtitle analysis (if not skipped)
        if not skip_analysis and parsed_subtitles:
            analyzed_subtitles = intelligent_subtitle_analysis(parsed_subtitles)
            pre_refinement_content = srt.compose(analyzed_subtitles)
        else:
            pre_refinement_content = original_srt_content
            if skip_analysis:
                print("Skipping intelligent subtitle analysis as requested.")
        
        # Step 2: AI-based refinement (existing functionality)
        refined_srt_text = get_refined_srt_content(pre_refinement_content)
        
        # Validate the refined SRT before writing
        try:
            # Parse to validate and re-compose to ensure canonical formatting by srt library
            parsed_refined_subs = list(srt.parse(refined_srt_text))
            # Re-index subtitles after AI processing, as AI might not maintain perfect sequence
            for i, sub in enumerate(parsed_refined_subs):
                sub.index = i + 1
            
            final_srt_output = srt.compose(parsed_refined_subs)

        except Exception as e_parse_refined:
            print(f"Error: AI output could not be parsed as valid SRT: {e_parse_refined}")
            print("AI's raw output that failed parsing was:\n---\n", refined_srt_text, "\n---")
            print(f"Saving AI's raw problematic output to: {output_srt_file}.problematic.txt")
            with open(f"{output_srt_file}.problematic.txt", 'w', encoding='utf-8') as f_prob:
                f_prob.write(refined_srt_text)
            
            # Fallback: write original content to output if AI failed badly
            print("Falling back to writing the original SRT content due to AI output parsing error.")
            final_srt_output = srt.compose(list(srt.parse(original_srt_content)))

        with open(output_srt_file, 'w', encoding='utf-8') as f:
            f.write(final_srt_output)
        print(f"Refined SRT content written to '{output_srt_file}'")

    except Exception as e:
        print(f"An error occurred during SRT refinement: {e}")
        # Fallback: try to write original srt to output to not break the pipeline completely
        try:
            with open(output_srt_file, 'w', encoding='utf-8') as f_out_err:
                f_out_err.write(original_srt_content)
            print(f"Wrote original SRT to '{output_srt_file}' due to an unhandled error in refinement.")
        except Exception as e_write_err:
            print(f"Critical error: Could not write original SRT to '{output_srt_file}' during error handling: {e_write_err}")
        sys.exit(1)

if __name__ == '__main__':
    main()