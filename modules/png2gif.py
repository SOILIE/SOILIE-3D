import math
import os
from PIL import Image


def _validate_interpolation_count(num_interpolated_frames):
    if (
        isinstance(num_interpolated_frames, bool)
        or not isinstance(num_interpolated_frames, int)
        or num_interpolated_frames < 0
    ):
        raise ValueError("num_interpolated_frames must be a non-negative integer")


def _duration_milliseconds(duration_per_frame):
    if (
        isinstance(duration_per_frame, bool)
        or not isinstance(duration_per_frame, (int, float))
        or not math.isfinite(duration_per_frame)
        or duration_per_frame <= 0
        or duration_per_frame != int(duration_per_frame)
        or int(duration_per_frame) % 10 != 0
    ):
        raise ValueError(
            "duration_per_frame must be a positive whole number of milliseconds "
            "divisible by 10 for exact GIF timing"
        )
    return int(duration_per_frame)


def gif_frame_durations(duration_per_frame, num_interpolated_frames=5):
    """Split one source interval into exact, near-uniform GIF durations.

    GIF stores time in centiseconds.  Cumulative rounding distributes the
    remainder across the source and interpolated frames while retaining the
    requested source-to-source duration exactly.
    """

    _validate_interpolation_count(num_interpolated_frames)
    duration_ms = _duration_milliseconds(duration_per_frame)
    subframe_count = num_interpolated_frames + 1
    total_centiseconds = duration_ms // 10
    if total_centiseconds < subframe_count:
        raise ValueError(
            "duration_per_frame must allow at least 10 milliseconds for every GIF subframe"
        )

    boundaries = [
        (index * total_centiseconds + subframe_count // 2) // subframe_count
        for index in range(1, subframe_count + 1)
    ]
    prior = 0
    durations = []
    for boundary in boundaries:
        durations.append((boundary - prior) * 10)
        prior = boundary
    return tuple(durations)


def interpolate_frames(frame1, frame2, num_interpolated_frames):
    """
    Interpolates frames between frame1 and frame2.

    :param frame1: The first frame (PIL Image).
    :param frame2: The second frame (PIL Image).
    :param num_interpolated_frames: The number of frames to interpolate between frame1 and frame2.
    :return: A list of interpolated frames.
    """
    _validate_interpolation_count(num_interpolated_frames)

    interpolated_frames = []
    for i in range(1, num_interpolated_frames + 1):
        alpha = i / (num_interpolated_frames + 1)
        interpolated_frame = Image.blend(frame1, frame2, alpha)
        interpolated_frames.append(interpolated_frame)
    return interpolated_frames


def animation_frames(images, num_interpolated_frames=5):
    """Return copied frames with an equal-cadence final-to-first transition."""

    _validate_interpolation_count(num_interpolated_frames)
    if not images:
        raise ValueError("At least one source image is required to build an animation")
    expected_format = (images[0].mode, images[0].size)
    if any((frame.mode, frame.size) != expected_format for frame in images[1:]):
        raise ValueError("All animation source images must have the same mode and dimensions")
    if len(images) == 1:
        return [images[0].copy()]

    all_frames = []
    for index, frame in enumerate(images):
        following = images[(index + 1) % len(images)]
        all_frames.append(frame.copy())
        all_frames.extend(interpolate_frames(frame, following, num_interpolated_frames))
    return all_frames


def animate_output(filename, path, duration_per_frame=400, num_interpolated_frames=5):
    png_files = sorted([os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith('.png') and
                        '_birds_eye_view' not in f.lower()])
    if not png_files:
        raise ValueError(f"No animation PNG frames found in {os.path.abspath(path)}")

    images = []
    all_frames = []
    try:
        # Copies release every source PNG before the GIF is written, which keeps
        # reruns and cleanup reliable on platforms with strict file locking.
        for png in png_files:
            with Image.open(png) as source:
                images.append(source.convert('RGB'))
        all_frames = animation_frames(images, num_interpolated_frames)
        if len(images) == 1:
            frame_durations = [_duration_milliseconds(duration_per_frame)]
        else:
            interval_durations = gif_frame_durations(
                duration_per_frame, num_interpolated_frames)
            frame_durations = list(interval_durations) * len(images)

        # Save as an infinitely looping GIF. The frame builder includes the
        # last-to-first blend, so the loop boundary has the same cadence as all
        # other source-frame transitions.
        all_frames[0].save(
            os.path.join(path, os.path.splitext(filename)[0] + '.gif'),
            save_all=True,
            append_images=all_frames[1:],
            duration=frame_durations,
            loop=0)
    finally:
        for frame in all_frames:
            frame.close()
        for source in images:
            source.close()
