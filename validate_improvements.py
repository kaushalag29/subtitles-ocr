#!/usr/bin/env python3

"""
Validation script to test improvements against real problematic examples.
"""

from refine_srt import has_narrative_text, likely_speaker_change, calculate_wpm

def test_narrative_detection():
    """Test narrative text detection with real examples"""
    print("=== Testing Narrative Text Detection ===")
    
    test_cases = [
        ("THEIR PATHS WILL NEVER CROSS...", True),
        ("OR SO THEY THOUGHT.", True),
        ("SO CLOSE YET SO FAR", True),
        ("THE FRAGRANT FLOWER BLOOMS WITH DIGNITY", True),
        ("SUBSCRIBE", True),
        ("I don't care about anything else!", False),
        ("What school we go to doesn't matter!", False),
        ("She said it decisively.", False),
        ("Hey, Rintaro! You free today?", False),
    ]
    
    for text, expected in test_cases:
        result = has_narrative_text(text)
        status = "✓" if result == expected else "✗"
        print(f"{status} '{text}' -> {result} (expected {expected})")
    
    print()

def test_speaker_change_detection():
    """Test speaker change detection with real examples"""
    print("=== Testing Speaker Change Detection ===")
    
    test_cases = [
        ("She said it decisively.", "Our customer, Miss Waguri.", True),
        ("Hey, Rintaro! You free today?", "Let's hang out!", True), 
        ("What school we go to doesn't matter!", "I don't care about anything else!", True),
        ("You talked to him, right? That blond guy.", "What was he like?", True),
        ("I think we should...", "go to the park today.", False),  # Continuation
        ("Are you serious?", "Yes, I am.", True),  # Q&A pattern
        ("I was waiting for you.", "Rintaro!", True),  # Different speakers likely
    ]
    
    for text1, text2, expected in test_cases:
        result = likely_speaker_change(text1, text2)
        status = "✓" if result == expected else "✗"
        print(f"{status} '{text1}' + '{text2}' -> {result} (expected {expected})")
    
    print()

def test_wpm_calculation():
    """Test WPM calculation with real examples"""
    print("=== Testing WPM Calculation ===")
    
    test_cases = [
        ("She said it decisively.", 2.0, "Normal"),  # ~90 WPM
        ("Because I came unannounced? It's just... I thought you'd stop coming. I mean, you go to Kikyo and I'm from Chidori.", 8.0, "Fast"),  # ~160 WPM
        ("SUBSCRIBE", 12.0, "Very Slow"),  # ~5 WPM
        ("This is a very long sentence that contains way too many words to be spoken comfortably in just one second.", 1.0, "Too Fast"),  # >1000 WPM
    ]
    
    for text, duration, category in test_cases:
        wpm = calculate_wpm(text, duration)
        if wpm > 250:
            actual_category = "Too Fast"
        elif wpm > 200:
            actual_category = "Fast"
        elif wpm < 50:
            actual_category = "Very Slow"
        else:
            actual_category = "Normal"
        
        status = "✓" if actual_category == category else "✗"
        print(f"{status} '{text[:50]}...' ({duration}s) -> {wpm:.1f} WPM ({actual_category}, expected {category})")
    
    print()

def analyze_real_example():
    """Analyze the real subtitle example provided"""
    print("=== Analyzing Real Subtitle Example ===")
    
    problematic_subtitles = [
        ("Because I came unannounced? It's just... I thought you'd stop coming. I mean, you go to Kikyo and I'm from Chidori.", 8.0),
        ("THEIR PATHS WILL NEVER CROSS...", 1.0),
        ("OR SO THEY THOUGHT.", 1.0),
        ("SO CLOSE YET SO FAR", 1.0),
        ("THE FRAGRANT FLOWER BLOOMS WITH DIGNITY", 4.0),
        ("SUBSCRIBE", 12.0),
    ]
    
    print("Subtitle Analysis:")
    for text, duration in problematic_subtitles:
        is_narrative = has_narrative_text(text)
        wpm = calculate_wpm(text, duration)
        
        print(f"Text: '{text}'")
        print(f"  Duration: {duration}s")
        print(f"  WPM: {wpm:.1f}")
        print(f"  Narrative: {is_narrative}")
        print(f"  Issues: {', '.join([
            'Too Fast' if wpm > 250 else '',
            'Too Slow' if wpm < 50 else '',
            'Narrative Text' if is_narrative else '',
            'Too Long' if len(text.split()) > 15 else ''
        ]).strip(', ')}")
        print()

def main():
    print("Subtitle Refinement Improvements Validation")
    print("=" * 50)
    
    test_narrative_detection()
    test_speaker_change_detection() 
    test_wpm_calculation()
    analyze_real_example()
    
    print("Validation complete!")

if __name__ == '__main__':
    main() 