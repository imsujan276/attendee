import os
import tempfile
from unittest.mock import MagicMock, patch

from django.test import TestCase

from accounts.models import Organization
from bots.bot_controller.bot_controller import BotController
from bots.bots_api_utils import BotCreationSource, create_bot
from bots.models import Project
from bots.serializers import RecordingSerializer


class TestRecordingAdditionalFilesSettings(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Test Organization")
        self.project = Project.objects.create(name="Test Project", organization=organization)

    def create_bot_with_recording_settings(self, recording_settings):
        return create_bot(
            data={"meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Test Bot", "recording_settings": recording_settings},
            source=BotCreationSource.API,
            project=self.project,
        )

    def test_additional_files_are_saved_for_mp4(self):
        bot, error = self.create_bot_with_recording_settings({"format": "mp4", "additional_files": ["audio_only", "video_only"]})
        self.assertIsNone(error)
        self.assertEqual(bot.recording_additional_files(), ["audio_only", "video_only"])

    def test_additional_files_default_to_empty(self):
        bot, error = self.create_bot_with_recording_settings({"format": "mp4"})
        self.assertIsNone(error)
        self.assertEqual(bot.recording_additional_files(), [])

    def test_additional_files_are_rejected_for_mp3(self):
        bot, error = self.create_bot_with_recording_settings({"format": "mp3", "additional_files": ["audio_only"]})
        self.assertIsNone(bot)
        self.assertIn("additional_files", str(error))

    def test_unknown_additional_file_is_rejected(self):
        bot, error = self.create_bot_with_recording_settings({"format": "mp4", "additional_files": ["subtitles"]})
        self.assertIsNone(bot)
        self.assertIsNotNone(error)


class TestRecordingAdditionalFileUrls(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Test Organization")
        project = Project.objects.create(name="Test Project", organization=organization)
        self.bot, _ = create_bot(data={"meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Test Bot"}, source=BotCreationSource.API, project=project)
        self.recording = self.bot.recordings.first()

    def test_urls_are_none_without_additional_files(self):
        self.assertIsNone(self.recording.audio_only_url)
        self.assertIsNone(self.recording.video_only_url)
        data = RecordingSerializer(self.recording).data
        self.assertIsNone(data["audio_only_url"])
        self.assertIsNone(data["video_only_url"])

    @patch("bots.models.remote_storage_url", side_effect=lambda file_field: f"https://storage.example.com/{file_field.name}")
    def test_urls_point_to_additional_files(self, mock_remote_storage_url):
        self.recording.audio_only_file = "recording-audio.mp3"
        self.recording.video_only_file = "recording-video.mp4"
        self.recording.save()

        data = RecordingSerializer(self.recording).data
        self.assertEqual(data["audio_only_url"], "https://storage.example.com/recording-audio.mp3")
        self.assertEqual(data["video_only_url"], "https://storage.example.com/recording-video.mp4")


class TestSaveAdditionalRecordingFiles(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Test Organization")
        project = Project.objects.create(name="Test Project", organization=organization)
        self.bot, _ = create_bot(
            data={"meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Test Bot", "recording_settings": {"format": "mp4", "additional_files": ["audio_only", "video_only"]}},
            source=BotCreationSource.API,
            project=project,
        )
        self.recording = self.bot.recordings.first()

        self.storage_directory = tempfile.mkdtemp()
        self.recording_file_location = os.path.join(self.storage_directory, "recording.mp4")
        with open(self.recording_file_location, "wb") as f:
            f.write(b"mp4 data")

        # Avoid running BotController.__init__, which sets up the meeting adapter
        self.controller = BotController.__new__(BotController)
        self.controller.bot_in_db = self.bot
        self.controller.get_recording_file_location = lambda: self.recording_file_location
        self.controller.get_recording_storage_directory = lambda: self.storage_directory
        self.uploaded_filenames = []

        def get_file_uploader(filename=None):
            self.uploaded_filenames.append(filename)
            return MagicMock(filename=filename)

        self.controller.get_file_uploader = get_file_uploader

    @patch("bots.bot_controller.bot_controller.subprocess.run")
    def test_creates_and_uploads_each_additional_file(self, mock_run):
        self.controller.save_additional_recording_files_with_no_error_handling()

        audio_filename = f"{self.bot.object_id}-{self.recording.object_id}-audio.mp3"
        video_filename = f"{self.bot.object_id}-{self.recording.object_id}-video.mp4"
        self.assertEqual(self.uploaded_filenames, [audio_filename, video_filename])

        audio_command = mock_run.call_args_list[0][0][0]
        self.assertEqual(audio_command[0], "ffmpeg")
        self.assertIn("-vn", audio_command)
        self.assertEqual(audio_command[-1], os.path.join(self.storage_directory, audio_filename))

        video_command = mock_run.call_args_list[1][0][0]
        self.assertIn("-an", video_command)
        self.assertEqual(video_command[-1], os.path.join(self.storage_directory, video_filename))

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.audio_only_file.name, audio_filename)
        self.assertEqual(self.recording.video_only_file.name, video_filename)

    @patch("bots.bot_controller.bot_controller.subprocess.run")
    def test_does_nothing_without_additional_files(self, mock_run):
        self.bot.settings["recording_settings"]["additional_files"] = []
        self.bot.save()

        self.controller.save_additional_recording_files_with_no_error_handling()

        mock_run.assert_not_called()
        self.assertEqual(self.uploaded_filenames, [])

    @patch("bots.bot_controller.bot_controller.subprocess.run", side_effect=Exception("ffmpeg failed"))
    def test_errors_do_not_propagate(self, mock_run):
        self.controller.save_additional_recording_files()

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.audio_only_file.name, "")
