import os
import sys
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import subprocess
import shlex
import tempfile
import shutil

# Standardized logging - configure more explicitly for subprocess capture
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Add startup logging to verify server is starting
print("="*50, flush=True)
print("SUBTITLES-OCR SERVER STARTING UP", flush=True)
print(f"Python executable: {sys.executable}", flush=True)
print(f"Working directory: {os.getcwd()}", flush=True)
print(f"Python path: {sys.path}", flush=True)
print("="*50, flush=True)

logger.info("Subtitles-OCR server initialization started")

app = FastAPI(title="Subtitles OCR API Server")

class OCRRequest(BaseModel):
    """Standardized request format for OCR processing"""
    input_video_path: str

class OCRResponse(BaseModel):
    """Standardized response format"""
    status: str
    message: str
    output_srt_path: Optional[str] = None
    error: Optional[str] = None

def execute_command_in_dir(cmd, cwd):
    """Execute subprocess command in a specific directory."""
    try:
        logger.info(f"Executing in {cwd}: {cmd}")
        process = subprocess.run(
            shlex.split(cmd),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(process.stdout)
        if process.stderr:
            logger.warning(process.stderr)
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}")
        logger.error(f"STDOUT: {e.stdout}")
        logger.error(f"STDERR: {e.stderr}")
        raise

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    logger.info("Health check endpoint called")
    print("SUBTITLES-OCR SERVER: Health check called", flush=True)
    return {"status": "healthy", "model": "subtitles-ocr"}

@app.post("/extract-subtitles", response_model=OCRResponse)
async def process_request(request: OCRRequest):
    """Standardized processing endpoint for subtitle extraction."""
    if not os.path.exists(request.input_video_path):
        raise HTTPException(status_code=404, detail=f"Input video not found at {request.input_video_path}")

    python_executable = sys.executable  # Get the path to the current python interpreter

    # Use an absolute path for the temporary directory to avoid relative path issues.
    with tempfile.TemporaryDirectory(prefix="ocr_temp_", dir=os.getcwd()) as temp_dir_abs:
        try:
            video_filename = os.path.basename(request.input_video_path)
            temp_video_path = os.path.join(temp_dir_abs, video_filename)
            shutil.copy(request.input_video_path, temp_video_path)

            # ffprobe runs from the server's CWD, so it needs the full path to the video in the temp dir.
            width_str = subprocess.check_output(shlex.split(f"ffprobe -v error -select_streams v:0 -show_entries stream=width -of default=noprint_wrappers=1:nokey=1 '{temp_video_path}'")).decode('utf-8').strip()
            height_str = subprocess.check_output(shlex.split(f"ffprobe -v error -select_streams v:0 -show_entries stream=height -of default=noprint_wrappers=1:nokey=1 '{temp_video_path}'")).decode('utf-8').strip()
            width = int(width_str)
            height = int(height_str)
            
            cropped_height = 400
            edges_trim_width = 50
            new_width = width - 2 * edges_trim_width
            y_start = height - cropped_height
            x_start = edges_trim_width
            
            cropped_video_filename = f"{video_filename}_video-cropped.mp4"
            upper_cropped_video_filename = f"{video_filename}_video-upper-cropped.mp4"

            # When executing inside the temp directory, use relative filenames for ffmpeg.
            execute_command_in_dir(f'ffmpeg -y -i "{video_filename}" -filter:v "crop={new_width}:{cropped_height}:{x_start}:{y_start}" -c:a copy "{cropped_video_filename}"', temp_dir_abs)
            execute_command_in_dir(f'ffmpeg -y -i "{video_filename}" -filter:v "crop={new_width}:250:{x_start}:0" -c:a copy "{upper_cropped_video_filename}"', temp_dir_abs)

            img_dir_name = f"{video_filename}_img"
            upper_img_dir_name = f"{video_filename}_upper_img"
            os.makedirs(os.path.join(temp_dir_abs, img_dir_name), exist_ok=True)
            os.makedirs(os.path.join(temp_dir_abs, upper_img_dir_name), exist_ok=True)
            
            execute_command_in_dir(f'ffmpeg -y -i "{cropped_video_filename}" -start_number 0 -vf "fps=1" -q:v 2 "{os.path.join(img_dir_name, "snap_%04d.png")}"', temp_dir_abs)
            execute_command_in_dir(f'ffmpeg -y -i "{upper_cropped_video_filename}" -start_number 0 -vf "fps=1" -q:v 2 "{os.path.join(upper_img_dir_name, "snap_%04d.png")}"', temp_dir_abs)
            
            results_json_filename = f"{video_filename}_results.json"
            upper_results_json_filename = f"{video_filename}_upper_results.json"
            
            # These scripts need to be called from the main subtitles-ocr directory, so we give them absolute paths to the files.
            execute_command_in_dir(f'{python_executable} do-ocr.py "{os.path.join(temp_dir_abs, img_dir_name)}" "{os.path.join(temp_dir_abs, results_json_filename)}"', ".")
            execute_command_in_dir(f'{python_executable} do-ocr.py "{os.path.join(temp_dir_abs, upper_img_dir_name)}" "{os.path.join(temp_dir_abs, upper_results_json_filename)}"', ".")
            
            ocr_srt_filename = f"{video_filename}.ocr.srt"
            execute_command_in_dir(f'{python_executable} gensrt.py "{os.path.join(temp_dir_abs, results_json_filename)}" "{os.path.join(temp_dir_abs, ocr_srt_filename)}" "{os.path.join(temp_dir_abs, upper_results_json_filename)}"', ".")

            execute_command_in_dir(f'srt-normalise -i "{os.path.join(temp_dir_abs, ocr_srt_filename)}" --inplace --debug', ".")
            
            refined_srt_filename = f"{video_filename}.ocr.refined.srt"
            execute_command_in_dir(f'{python_executable} refine_srt.py "{os.path.join(temp_dir_abs, ocr_srt_filename)}" "{os.path.join(temp_dir_abs, refined_srt_filename)}"', ".")
            
            final_srt_filename = f"{video_filename}.ocr.final.srt"
            final_srt_path_in_temp = os.path.join(temp_dir_abs, final_srt_filename)
            
            refined_srt_path_in_temp = os.path.join(temp_dir_abs, refined_srt_filename)
            if os.path.exists(refined_srt_path_in_temp):
                shutil.move(refined_srt_path_in_temp, final_srt_path_in_temp)
            else:
                shutil.move(os.path.join(temp_dir_abs, ocr_srt_filename), final_srt_path_in_temp)

            final_output_dir = os.path.abspath("output")
            os.makedirs(final_output_dir, exist_ok=True)
            final_output_path = os.path.join(final_output_dir, final_srt_filename)
            shutil.copy(final_srt_path_in_temp, final_output_path)

            return OCRResponse(
                status="success",
                message="Subtitles extracted successfully",
                output_srt_path=final_output_path
            )
        except Exception as e:
            logger.error(f"Processing error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/shutdown")
async def shutdown():
    logger.info("Shutdown request received")
    os._exit(0)
    return {"status": "shutdown", "message": "Server shutting down"}

if __name__ == "__main__":
    print("="*50, flush=True)
    print("SUBTITLES-OCR SERVER: Starting uvicorn server", flush=True)
    print("="*50, flush=True)
    logger.info("Starting uvicorn server on 0.0.0.0:8008")
    
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8008,
        log_level="info",
        access_log=True
    ) 