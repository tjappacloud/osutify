"""Audio playback wrapper using pygame.mixer."""

import pygame.mixer


def init_audio():
    """Initialize pygame mixer with exception handling."""
    try:
        pygame.mixer.init()
        return True
    except Exception as e:
        print(f"Audio init failed: {e}")
        return False


def is_audio_initialized():
    """Check if pygame mixer is initialized."""
    try:
        return pygame.mixer.get_init() is not None
    except Exception:
        return False


def load_and_play(path: str):
    """Load and play an audio file."""
    if not is_audio_initialized():
        return False
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        return True
    except Exception as e:
        print(f"Playback error: {e}")
        return False


def pause():
    """Pause playback."""
    if not is_audio_initialized():
        return False
    try:
        pygame.mixer.music.pause()
        return True
    except Exception:
        return False


def unpause():
    """Unpause playback."""
    if not is_audio_initialized():
        return False
    try:
        pygame.mixer.music.unpause()
        return True
    except Exception:
        return False


def stop():
    """Stop playback."""
    if not is_audio_initialized():
        return
    try:
        pygame.mixer.music.stop()
    except Exception:
        pass


def is_busy():
    """Check if music is currently playing."""
    if not is_audio_initialized():
        return False
    try:
        return pygame.mixer.music.get_busy()
    except Exception:
        return False


def get_pos():
    """Get current playback position in milliseconds."""
    if not is_audio_initialized():
        return 0
    try:
        return pygame.mixer.music.get_pos()
    except Exception:
        return 0


def set_volume(vol: float):
    """Set playback volume (0.0 - 1.0)."""
    if not is_audio_initialized():
        return
    try:
        pygame.mixer.music.set_volume(vol)
    except Exception:
        pass


def seek_set_pos(pos_sec: float):
    """Seek using set_pos method."""
    if not is_audio_initialized():
        return False
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.set_pos(float(pos_sec))
        pygame.mixer.music.play()
        return True
    except Exception:
        return False


def seek_play_start(pos_sec: float):
    """Seek using play start parameter."""
    if not is_audio_initialized():
        return False
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.play(0, float(pos_sec))
        return True
    except Exception:
        return False


def restart_playback(path: str):
    """Restart playback from beginning."""
    if not is_audio_initialized():
        return False
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        return True
    except Exception:
        return False
