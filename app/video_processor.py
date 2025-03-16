import logging
import tempfile
import os
import ffmpeg
import time
from typing import Tuple
from humanize import naturalsize

logger = logging.getLogger(__name__)


def get_video_details(probe_data: dict) -> dict:
    """Extract and format relevant video details from probe data"""
    try:
        format_info = probe_data['format']
        video_stream = next(
            s for s in probe_data['streams'] if s['codec_type'] == 'video')
        audio_stream = next(
            (s for s in probe_data['streams'] if s['codec_type'] == 'audio'), None)

        details = {
            'format': format_info.get('format_name', 'unknown'),
            'duration': float(format_info.get('duration', 0)),
            'size': int(format_info.get('size', 0)),
            'bitrate': int(format_info.get('bit_rate', 0)),
            'video_codec': video_stream.get('codec_name', 'unknown'),
            'width': int(video_stream.get('width', 0)),
            'height': int(video_stream.get('height', 0)),
            'fps': eval(video_stream.get('avg_frame_rate', '0/1')),
            'audio_codec': audio_stream.get('codec_name', 'none') if audio_stream else 'none'
        }
        return details
    except Exception as e:
        logger.warning(f"Error getting video details: {e}")
        return {}


def is_video_compatible(video_path: str) -> Tuple[bool, dict]:
    """
    Check if video is already in a compatible format for Telegram.
    Returns (is_compatible, video_info)
    """
    try:
        start_time = time.time()
        logger.info(f"Analyzing video file: {video_path}")

        probe = ffmpeg.probe(video_path)
        video_details = get_video_details(probe)

        # Log detailed video information
        logger.info("Video details:")
        logger.info(f"  Format: {video_details['format']}")
        logger.info(f"  Size: {naturalsize(video_details['size'])}")
        logger.info(f"  Duration: {video_details['duration']:.2f}s")
        logger.info(
            f"  Dimensions: {video_details['width']}x{video_details['height']}")
        logger.info(f"  Video codec: {video_details['video_codec']}")
        logger.info(f"  Audio codec: {video_details['audio_codec']}")
        logger.info(f"  Bitrate: {video_details['bitrate'] // 1000}kbps")
        logger.info(f"  FPS: {video_details['fps']:.2f}")

        # Check compatibility
        is_h264 = video_details['video_codec'].lower() == 'h264'
        is_mp4 = 'mp4' in video_details['format'].lower().split(',')
        audio_compatible = video_details['audio_codec'].lower(
        ) == 'aac' or video_details['audio_codec'] == 'none'

        is_compatible = all([is_h264, is_mp4, audio_compatible])

        analysis_time = time.time() - start_time
        logger.info(f"Compatibility check completed in {analysis_time:.2f}s:")
        logger.info(f"  H.264 video: {is_h264}")
        logger.info(f"  MP4 container: {is_mp4}")
        logger.info(f"  AAC audio: {audio_compatible}")
        logger.info(f"  Overall compatible: {is_compatible}")

        return is_compatible, video_details
    except Exception as e:
        logger.warning(f"Error checking video compatibility: {e}")
        return False, {}


async def process_video_file(video_data: bytes, filename: str) -> Tuple[bytes, int, int]:
    """
    Process video to ensure correct format for Telegram.
    Optimized to skip processing if video is already compatible.
    Returns tuple of (processed_video_bytes, width, height)
    """
    start_time = time.time()
    logger.info(f"Starting video processing for {filename}")
    logger.info(f"Input video size: {naturalsize(len(video_data))}")

    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_in:
        temp_in.write(video_data)
        temp_in.flush()
        logger.info(f"Temporary input file created: {temp_in.name}")

        try:
            # Check if video needs processing
            is_compatible, video_info = is_video_compatible(temp_in.name)
            width = video_info.get('width', 0)
            height = video_info.get('height', 0)

            if is_compatible:
                process_time = time.time() - start_time
                logger.info(
                    f"Video is already compatible, processing completed in {process_time:.2f}s")
                return video_data, width, height

            logger.info("Video needs conversion, starting FFmpeg process...")
            # Create a temporary output file
            temp_out = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
            temp_out.close()

            # Process video while maintaining aspect ratio
            stream = ffmpeg.input(temp_in.name)
            stream = ffmpeg.output(
                stream,
                temp_out.name,
                vcodec='h264',
                acodec='aac',
                format='mp4',
                video_bitrate='2M',
                audio_bitrate='128k',
                vf=f'scale={width}:{height}:force_original_aspect_ratio=decrease',
                strict='experimental'
            )

            # Run FFmpeg with progress logging
            conversion_start = time.time()
            logger.info("Starting FFmpeg conversion...")
            ffmpeg.run(stream, capture_stdout=True,
                       capture_stderr=True, overwrite_output=True)
            conversion_time = time.time() - conversion_start
            logger.info(
                f"FFmpeg conversion completed in {conversion_time:.2f}s")

            # Read and analyze the processed video
            with open(temp_out.name, 'rb') as f:
                processed_data = f.read()

            # Log final video details
            final_details = get_video_details(ffmpeg.probe(temp_out.name))
            logger.info("Processed video details:")
            logger.info(f"  Size: {naturalsize(len(processed_data))}")
            logger.info(f"  Duration: {final_details['duration']:.2f}s")
            logger.info(
                f"  Dimensions: {final_details['width']}x{final_details['height']}")
            logger.info(f"  Bitrate: {final_details['bitrate'] // 1000}kbps")

            total_time = time.time() - start_time
            logger.info(f"Total processing time: {total_time:.2f}s")

            os.unlink(temp_out.name)
            return processed_data, width, height

        finally:
            os.unlink(temp_in.name)
