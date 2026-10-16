"""
Tests for transcribing a bot's audio after the meeting ends (elevenlabs.transcription_timing = "after_meeting").
"""

import os
from unittest import mock

import jsonschema
import requests
from django.test import override_settings
from django.test.testcases import TransactionTestCase

from bots.models import (
    AudioChunk,
    Bot,
    BotEventManager,
    BotEventTypes,
    BotStates,
    Credentials,
    Organization,
    Participant,
    Project,
    Recording,
    RecordingStates,
    RecordingTranscriptionStates,
    RecordingTypes,
    TranscriptionFailureReasons,
    TranscriptionProviders,
    TranscriptionSettings,
    TranscriptionTypes,
    Utterance,
    WebhookDeliveryAttempt,
    WebhookSecret,
    WebhookSubscription,
    WebhookTriggerTypes,
)
from bots.serializers import TRANSCRIPTION_SETTINGS_SCHEMA
from bots.tasks.process_async_transcription_task import create_utterances_for_audio_chunks
from bots.tasks.process_utterance_group_for_async_transcription_task import process_utterance_group_for_async_transcription
from bots.transcription_utils import get_elevenlabs_request_timeout_seconds


def elevenlabs_response(status_code=200, body=None):
    response = mock.Mock(status_code=status_code, text="")
    response.json.return_value = body or {}
    return response


@mock.patch.dict(os.environ, {"AWS_RECORDING_STORAGE_BUCKET_NAME": "test-bucket"})
@override_settings(CHARGE_CREDITS_FOR_BOTS=False, CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class PostMeetingTranscriptionTestCase(TransactionTestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Test Org", centicredits=10000)
        self.project = Project.objects.create(name="Test Project", organization=self.organization)
        self.bot = Bot.objects.create(
            project=self.project,
            name="Test Bot",
            meeting_url="https://meet.google.com/abc-defg-hij",
            state=BotStates.POST_PROCESSING,
            settings={"transcription_settings": {"elevenlabs": {"model_id": "scribe_v2", "transcription_timing": "after_meeting"}}},
        )
        self.recording = Recording.objects.create(
            bot=self.bot,
            recording_type=RecordingTypes.AUDIO_AND_VIDEO,
            transcription_type=TranscriptionTypes.NON_REALTIME,
            transcription_provider=TranscriptionProviders.ELEVENLABS,
            is_default_recording=True,
            state=RecordingStates.IN_PROGRESS,
        )
        credentials = Credentials.objects.create(project=self.project, credential_type=Credentials.CredentialTypes.ELEVENLABS)
        credentials.set_credentials({"api_key": "test_elevenlabs_api_key"})

        self.alice = Participant.objects.create(bot=self.bot, uuid="alice", full_name="Alice")
        self.bob = Participant.objects.create(bot=self.bot, uuid="bob", full_name="Bob")

        WebhookSecret.objects.create(project=self.project)
        WebhookSubscription.objects.create(
            project=self.project,
            url="https://example.com/webhook",
            triggers=[WebhookTriggerTypes.TRANSCRIPT_UPDATE],
            is_active=True,
        )

    def _create_audio_chunk(self, participant, timestamp_ms, duration_ms=1000, sample_rate=16000):
        return AudioChunk.objects.create(
            recording=self.recording,
            participant=participant,
            audio_blob=b"\x00\x00" * (sample_rate * duration_ms // 1000),
            timestamp_ms=timestamp_ms,
            duration_ms=duration_ms,
            sample_rate=sample_rate,
        )

    def _end_bot(self):
        BotEventManager.create_event(bot=self.bot, event_type=BotEventTypes.POST_PROCESSING_COMPLETED)


@mock.patch("bots.tasks.deliver_webhook_task.deliver_webhook")
@mock.patch("bots.transcription_utils.get_mp3_for_utterance_group", return_value=b"fake-mp3-data")
@mock.patch("bots.transcription_utils.requests.post")
class TestPostMeetingTranscription(PostMeetingTranscriptionTestCase):
    def test_all_audio_is_sent_to_elevenlabs_in_one_request_when_the_bot_ends(self, mock_post, mock_get_mp3, mock_deliver_webhook):
        self._create_audio_chunk(self.alice, timestamp_ms=0)
        self._create_audio_chunk(self.bob, timestamp_ms=1500)

        # The two 1 second chunks are joined with 3 seconds of silence between them, so Bob's audio starts at 4s
        mock_post.return_value = elevenlabs_response(
            body={
                "text": "के भयो? Real-time थाहा छ?",
                "language_code": "nep",
                "language_probability": 0.4,
                "words": [
                    {"text": "के", "start": 0.1, "end": 0.3, "type": "word"},
                    {"text": " ", "start": 0.3, "end": 0.35, "type": "spacing"},
                    {"text": "भयो?", "start": 0.35, "end": 0.8, "type": "word"},
                    {"text": "Real-time", "start": 4.1, "end": 4.5, "type": "word"},
                    {"text": " ", "start": 4.5, "end": 4.55, "type": "spacing"},
                    {"text": "थाहा", "start": 4.55, "end": 4.7, "type": "word"},
                    {"text": " ", "start": 4.7, "end": 4.75, "type": "spacing"},
                    {"text": "छ?", "start": 4.75, "end": 4.9, "type": "word"},
                ],
            }
        )

        self._end_bot()

        self.assertEqual(mock_post.call_count, 1)
        data = mock_post.call_args[1]["data"]
        self.assertEqual(data["model_id"], "scribe_v2")
        self.assertNotIn("language_code", data)

        utterances = {u.participant_id: u for u in Utterance.objects.filter(recording=self.recording)}
        self.assertEqual(utterances[self.alice.id].transcription["transcript"], "के भयो?")
        self.assertEqual(utterances[self.bob.id].transcription["transcript"], "Real-time थाहा छ?")
        self.assertEqual(utterances[self.bob.id].transcription["language"], "nep")
        self.assertAlmostEqual(utterances[self.bob.id].transcription["words"][0]["start"], 0.1)

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.COMPLETE)
        self.assertEqual(WebhookDeliveryAttempt.objects.filter(bot=self.bot, webhook_trigger_type=WebhookTriggerTypes.TRANSCRIPT_UPDATE).count(), 2)

        # The audio is no longer needed once it's transcribed
        for audio_chunk in AudioChunk.objects.filter(recording=self.recording):
            self.assertEqual(bytes(audio_chunk.audio_blob), b"")

    def test_recording_transcription_fails_when_elevenlabs_rejects_the_credentials(self, mock_post, mock_get_mp3, mock_deliver_webhook):
        self._create_audio_chunk(self.alice, timestamp_ms=0)
        mock_post.return_value = elevenlabs_response(status_code=401)

        self._end_bot()

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.FAILED)
        self.assertEqual(self.recording.transcription_failure_data, {"failure_reasons": [TranscriptionFailureReasons.CREDENTIALS_INVALID]})

    def test_nothing_is_transcribed_when_no_one_spoke(self, mock_post, mock_get_mp3, mock_deliver_webhook):
        self._end_bot()

        mock_post.assert_not_called()
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.NOT_STARTED)

    def test_live_transcription_is_not_redone_after_the_meeting(self, mock_post, mock_get_mp3, mock_deliver_webhook):
        self.bot.settings = {"transcription_settings": {"elevenlabs": {"model_id": "scribe_v2", "transcription_timing": "live"}}}
        self.bot.save()
        self._create_audio_chunk(self.alice, timestamp_ms=0)

        self._end_bot()

        mock_post.assert_not_called()
        self.assertFalse(Utterance.objects.filter(recording=self.recording).exists())


class TestTranscriptionTimingSetting(TransactionTestCase):
    def test_transcribes_live_by_default(self):
        self.assertFalse(TranscriptionSettings({"elevenlabs": {"model_id": "scribe_v2"}}).transcribe_after_meeting())

    def test_transcribes_after_meeting_when_requested(self):
        self.assertTrue(TranscriptionSettings({"elevenlabs": {"model_id": "scribe_v2", "transcription_timing": "after_meeting"}}).transcribe_after_meeting())

    def test_other_providers_transcribe_live(self):
        self.assertFalse(TranscriptionSettings({"deepgram": {}}).transcribe_after_meeting())
        self.assertFalse(TranscriptionSettings(None).transcribe_after_meeting())

    def test_schema_accepts_only_known_timings(self):
        jsonschema.validate({"elevenlabs": {"model_id": "scribe_v2", "transcription_timing": "after_meeting"}}, TRANSCRIPTION_SETTINGS_SCHEMA)
        with self.assertRaises(jsonschema.exceptions.ValidationError):
            jsonschema.validate({"elevenlabs": {"model_id": "scribe_v2", "transcription_timing": "tomorrow"}}, TRANSCRIPTION_SETTINGS_SCHEMA)


class TestElevenLabsRequestTimeout(TransactionTestCase):
    def test_short_audio_gets_the_minimum_timeout(self):
        self.assertEqual(get_elevenlabs_request_timeout_seconds(20 * 1000), (10, 5 * 60))

    def test_timeout_grows_with_the_audio_length(self):
        self.assertEqual(get_elevenlabs_request_timeout_seconds(30 * 60 * 1000), (10, 30 * 60))

    def test_timeout_stays_under_the_task_time_limit(self):
        self.assertEqual(get_elevenlabs_request_timeout_seconds(2 * 60 * 60 * 1000), (10, 55 * 60))

    @mock.patch.dict(os.environ, {"ELEVENLABS_TRANSCRIPTION_MIN_TIMEOUT_SECONDS": "60", "ELEVENLABS_TRANSCRIPTION_TIMEOUT_SECONDS_PER_AUDIO_SECOND": "2"})
    def test_timeout_can_be_adjusted_with_env_vars(self):
        self.assertEqual(get_elevenlabs_request_timeout_seconds(10 * 1000), (10, 60))
        self.assertEqual(get_elevenlabs_request_timeout_seconds(10 * 60 * 1000), (10, 20 * 60))


@mock.patch.dict(os.environ, {"AWS_RECORDING_STORAGE_BUCKET_NAME": "test-bucket"})
@override_settings(CHARGE_CREDITS_FOR_BOTS=False, CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
@mock.patch("bots.tasks.deliver_webhook_task.deliver_webhook")
@mock.patch("bots.transcription_utils.get_mp3_for_utterance_group", return_value=b"fake-mp3-data")
@mock.patch("bots.transcription_utils.requests.post")
class TestPostMeetingTranscriptionTimeout(PostMeetingTranscriptionTestCase):
    def test_request_timeout_is_based_on_the_group_audio_length(self, mock_post, mock_get_mp3, mock_deliver_webhook):
        self._create_audio_chunk(self.alice, timestamp_ms=0, duration_ms=10 * 60 * 1000)
        self._create_audio_chunk(self.bob, timestamp_ms=10 * 60 * 1000, duration_ms=10 * 60 * 1000)
        mock_post.return_value = elevenlabs_response(body={"text": "", "words": []})

        self._end_bot()

        self.assertEqual(mock_post.call_args[1]["timeout"], (10, 20 * 60))

    @mock.patch("bots.tasks.process_utterance_group_for_async_transcription_task.process_utterance_group_for_async_transcription.retry")
    def test_timed_out_request_is_retried(self, mock_retry, mock_post, mock_get_mp3, mock_deliver_webhook):
        self._create_audio_chunk(self.alice, timestamp_ms=0)
        mock_post.side_effect = requests.exceptions.ReadTimeout("read timed out")
        # Celery raises whatever retry() returns, so hand back the original error instead of scheduling a retry
        mock_retry.side_effect = lambda exc=None, **kwargs: exc

        utterances = create_utterances_for_audio_chunks(self.recording, async_transcription=None)
        with self.assertRaisesRegex(Exception, "Retryable failure"):
            process_utterance_group_for_async_transcription.run([u.id for u in utterances])

        utterance = Utterance.objects.get(id=utterances[0].id)
        self.assertIsNone(utterance.failure_data)
        self.assertEqual(utterance.transcription_attempt_count, 1)
