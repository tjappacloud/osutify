"""Entry point for osu! MP3 Browser application."""

from osu_mp3_browser import OsuMP3Browser


def main():
    """Run the osu! MP3 Browser application."""
    app = OsuMP3Browser()
    app.mainloop()


if __name__ == '__main__':
    main()
