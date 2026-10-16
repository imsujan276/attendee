import os
import tempfile
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Organization, User, UserRole
from bots.bot_controller.bot_controller import BotController
from bots.bots_api_utils import BotCreationSource, create_bot
from bots.models import Bot, Credentials, Project, Recording, TranscriptionProviders
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


class TestActiveTranscriptionProvider(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Test Organization")
        self.project = Project.objects.create(name="Test Project", organization=organization)

    def create_bot(self, **data):
        return create_bot(data={"meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Test Bot", **data}, source=BotCreationSource.API, project=self.project)

    def make_elevenlabs_active(self):
        credentials = Credentials.objects.create(project=self.project, credential_type=Credentials.CredentialTypes.ELEVENLABS)
        credentials.set_credentials({"api_key": "test-key"})
        self.project.active_transcription_provider = "elevenlabs"
        self.project.save()

    def test_platform_defaults_without_active_provider(self):
        bot, error = self.create_bot()
        self.assertIsNone(error)
        self.assertEqual(bot.settings["transcription_settings"], {"meeting_closed_captions": {}})

    def test_active_provider_is_used_when_bot_has_no_transcription_settings(self):
        self.make_elevenlabs_active()
        bot, error = self.create_bot()
        self.assertIsNone(error)
        self.assertEqual(bot.settings["transcription_settings"], {"elevenlabs": {"model_id": "scribe_v2"}})
        self.assertEqual(bot.recordings.get().transcription_provider, TranscriptionProviders.ELEVENLABS)

    def test_bot_transcription_settings_override_active_provider(self):
        self.make_elevenlabs_active()
        bot, error = self.create_bot(transcription_settings={"meeting_closed_captions": {}})
        self.assertIsNone(error)
        self.assertEqual(bot.settings["transcription_settings"], {"meeting_closed_captions": {}})

    def test_active_provider_without_credentials_falls_back_to_platform_default(self):
        self.project.active_transcription_provider = "elevenlabs"
        self.project.save()
        bot, error = self.create_bot()
        self.assertIsNone(error)
        self.assertEqual(bot.settings["transcription_settings"], {"meeting_closed_captions": {}})


class TestSetActiveTranscriptionProviderView(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Test Organization")
        self.user = User.objects.create_user(username="admin", email="admin@example.com", password="testpassword123", role=UserRole.ADMIN, organization=organization)
        self.project = Project.objects.create(name="Test Project", organization=organization)
        self.client = Client()
        self.client.force_login(self.user)

    def set_provider(self, provider):
        return self.client.post(reverse("projects:set-active-transcription-provider", kwargs={"object_id": self.project.object_id}), {"active_transcription_provider": provider})

    def test_sets_provider_with_credentials(self):
        Credentials.objects.create(project=self.project, credential_type=Credentials.CredentialTypes.ELEVENLABS).set_credentials({"api_key": "test-key"})
        response = self.set_provider("elevenlabs")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Saved")
        self.project.refresh_from_db()
        self.assertEqual(self.project.active_transcription_provider, "elevenlabs")

    def test_rejects_provider_without_credentials(self):
        response = self.set_provider("elevenlabs")
        self.assertContains(response, "Add ElevenLabs credentials")
        self.project.refresh_from_db()
        self.assertIsNone(self.project.active_transcription_provider)

    def test_clears_provider(self):
        self.project.active_transcription_provider = "elevenlabs"
        self.project.save()
        self.set_provider("")
        self.project.refresh_from_db()
        self.assertIsNone(self.project.active_transcription_provider)

    def test_rejects_unknown_provider(self):
        self.assertEqual(self.set_provider("not_a_provider").status_code, 400)


@patch("bots.projects_views.launch_adhoc_bot_from_view")
class TestCreateBotViewOptions(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name="Test Organization", centicredits=10000)
        self.user = User.objects.create_user(username="admin", email="admin@example.com", password="testpassword123", role=UserRole.ADMIN, organization=organization)
        self.project = Project.objects.create(name="Test Project", organization=organization)
        self.client = Client()
        self.client.force_login(self.user)

    def post_create_bot(self, data):
        return self.client.post(reverse("bots:create-bot", kwargs={"object_id": self.project.object_id}), {"meeting_url": "https://meet.google.com/abc-defg-hij", "bot_name": "Test Bot", **data})

    def test_default_form_keeps_default_settings(self, mock_launch):
        response = self.post_create_bot({"recording_format": "mp4", "transcription_provider": "default"})
        self.assertEqual(response.status_code, 200)

        bot = Bot.objects.get(project=self.project)
        self.assertEqual(bot.recording_format(), "mp4")
        self.assertEqual(bot.recording_additional_files(), [])
        self.assertEqual(bot.settings["transcription_settings"], {"meeting_closed_captions": {}})

    def test_recording_format_and_additional_files(self, mock_launch):
        response = self.post_create_bot({"recording_format": "mp4", "additional_files": ["audio_only", "video_only"]})
        self.assertEqual(response.status_code, 200)

        bot = Bot.objects.get(project=self.project)
        self.assertEqual(bot.recording_format(), "mp4")
        self.assertEqual(bot.recording_additional_files(), ["audio_only", "video_only"])

    def test_additional_files_ignored_for_audio_only_recording(self, mock_launch):
        response = self.post_create_bot({"recording_format": "mp3", "additional_files": ["video_only"]})
        self.assertEqual(response.status_code, 200)

        bot = Bot.objects.get(project=self.project)
        self.assertEqual(bot.recording_format(), "mp3")
        self.assertEqual(bot.recording_additional_files(), [])

    def test_elevenlabs_transcription(self, mock_launch):
        response = self.post_create_bot({"transcription_provider": "elevenlabs", "elevenlabs_model_id": "scribe_v2", "elevenlabs_no_verbatim": "on"})
        self.assertEqual(response.status_code, 200)

        bot = Bot.objects.get(project=self.project)
        self.assertEqual(bot.settings["transcription_settings"], {"elevenlabs": {"model_id": "scribe_v2", "tag_audio_events": False, "no_verbatim": True}})
        self.assertEqual(Recording.objects.get(bot=bot).get_transcription_provider_display(), "ElevenLabs")

    def test_no_verbatim_only_sent_for_scribe_v2(self, mock_launch):
        response = self.post_create_bot({"transcription_provider": "elevenlabs", "elevenlabs_model_id": "scribe_v1", "elevenlabs_no_verbatim": "on", "elevenlabs_tag_audio_events": "on"})
        self.assertEqual(response.status_code, 200)

        bot = Bot.objects.get(project=self.project)
        self.assertEqual(bot.settings["transcription_settings"], {"elevenlabs": {"model_id": "scribe_v1", "tag_audio_events": True}})

    def test_metadata_is_saved(self, mock_launch):
        response = self.post_create_bot({"metadata": '{"customer_id": "abc123"}'})
        self.assertEqual(response.status_code, 200)

        bot = Bot.objects.get(project=self.project)
        self.assertEqual(bot.metadata, {"customer_id": "abc123"})

    def test_blank_metadata_is_ignored(self, mock_launch):
        response = self.post_create_bot({"metadata": "  "})
        self.assertEqual(response.status_code, 200)

        bot = Bot.objects.get(project=self.project)
        self.assertIsNone(bot.metadata)

    def test_invalid_metadata_json_is_rejected(self, mock_launch):
        response = self.post_create_bot({"metadata": "{not json"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Metadata must be valid JSON", response.content.decode())
        self.assertFalse(Bot.objects.filter(project=self.project).exists())

    def test_metadata_validation_errors_are_returned(self, mock_launch):
        response = self.post_create_bot({"metadata": '["not", "an", "object"]'})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Metadata must be an object", response.content.decode())
