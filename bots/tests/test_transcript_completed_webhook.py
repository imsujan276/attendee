"""
Tests for the transcript.completed webhook, which is delivered once a bot's recording and its transcription have both finished.
"""

from unittest import mock

from django.test import SimpleTestCase

from bots.models import (
    RecordingManager,
    RecordingStates,
    RecordingTranscriptionStates,
    TranscriptionFailureReasons,
    Utterance,
    WebhookDeliveryAttempt,
    WebhookSubscription,
    WebhookTriggerTypes,
)
from bots.tasks.deliver_webhook_task import deliver_webhook as send_webhook
from bots.tasks.process_utterance_task import process_utterance
from bots.tests.test_post_meeting_transcription import PostMeetingTranscriptionTestCase, elevenlabs_response

RECORDING_URL = "https://test-bucket.s3.amazonaws.com/recording.mp4?signature=abc"


class TranscriptCompletedWebhookTestCase(PostMeetingTranscriptionTestCase):
    def setUp(self):
        super().setUp()
        WebhookSubscription.objects.create(
            project=self.project,
            url="https://example.com/transcript-completed",
            triggers=[WebhookTriggerTypes.TRANSCRIPT_COMPLETED],
            is_active=True,
        )

    def _use_live_transcription(self):
        self.bot.settings = {"transcription_settings": {"elevenlabs": {"model_id": "scribe_v2", "transcription_timing": "live"}}}
        self.bot.save()

    def _create_utterance(self, participant, timestamp_ms, transcription=None, failure_data=None):
        return Utterance.objects.create(
            recording=self.recording,
            participant=participant,
            source=Utterance.Sources.PER_PARTICIPANT_AUDIO,
            timestamp_ms=timestamp_ms,
            duration_ms=1000,
            transcription=transcription,
            failure_data=failure_data,
        )

    # Creates an utterance the same way the bot controller does when transcribing during the meeting
    def _create_live_utterance(self, participant, timestamp_ms):
        audio_chunk = self._create_audio_chunk(participant, timestamp_ms=timestamp_ms)
        utterance = Utterance.objects.create(
            source=Utterance.Sources.PER_PARTICIPANT_AUDIO,
            recording=self.recording,
            participant=participant,
            audio_chunk=audio_chunk,
            timestamp_ms=audio_chunk.timestamp_ms,
            duration_ms=audio_chunk.duration_ms,
        )
        RecordingManager.set_recording_transcription_in_progress(self.recording)
        return utterance

    def _transcript_completed_attempts(self):
        return WebhookDeliveryAttempt.objects.filter(bot=self.bot, webhook_trigger_type=WebhookTriggerTypes.TRANSCRIPT_COMPLETED)


@mock.patch("bots.models.Recording.url", new_callable=mock.PropertyMock, return_value=RECORDING_URL)
@mock.patch("bots.tasks.deliver_webhook_task.deliver_webhook")
class TestTranscriptCompletedWebhookForLiveTranscription(TranscriptCompletedWebhookTestCase):
    def test_delivered_with_transcript_and_recording_url_when_the_bot_ends(self, mock_deliver_webhook, mock_url):
        self._use_live_transcription()
        self._create_utterance(self.bob, timestamp_ms=2000, transcription={"transcript": "Hi Alice"})
        self._create_utterance(self.alice, timestamp_ms=0, transcription={"transcript": "Hello Bob", "words": [{"word": "Hello", "start": 0.0, "end": 0.4}]})
        self._create_utterance(self.alice, timestamp_ms=4000, transcription={"transcript": ""})
        RecordingManager.set_recording_transcription_in_progress(self.recording)

        self._end_bot()

        self.assertEqual(self._transcript_completed_attempts().count(), 1)
        payload = self._transcript_completed_attempts().first().payload
        self.assertEqual(payload["state"], "complete")
        self.assertIsNone(payload["failure_data"])
        self.assertEqual(payload["recording"]["url"], RECORDING_URL)
        self.assertEqual([u["transcription"]["transcript"] for u in payload["transcript"]], ["Hello Bob", "Hi Alice"])
        self.assertEqual(payload["transcript"][0]["speaker_name"], "Alice")
        self.assertEqual(payload["transcript"][0]["speaker_uuid"], "alice")
        self.assertEqual(payload["transcript"][0]["transcription"]["words"], [{"word": "Hello", "start": 0.0, "end": 0.4}])

    @mock.patch("bots.tasks.process_utterance_task.pcm_to_mp3", return_value=b"fake-mp3-data")
    @mock.patch("bots.transcription_utils.requests.post")
    def test_delivered_after_live_elevenlabs_transcription_when_the_bot_ends(self, mock_post, mock_pcm_to_mp3, mock_deliver_webhook, mock_url):
        self._use_live_transcription()
        mock_post.return_value = elevenlabs_response(body={"text": "Hello Bob", "language_code": "eng", "language_probability": 0.9, "words": [{"text": "Hello", "start": 0.1, "end": 0.5, "type": "word"}]})

        utterance = self._create_live_utterance(self.alice, timestamp_ms=0)
        process_utterance.delay(utterance.id)

        # Nothing is delivered while the meeting is still going
        self.assertEqual(self._transcript_completed_attempts().count(), 0)

        self._end_bot()

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(self._transcript_completed_attempts().count(), 1)
        payload = self._transcript_completed_attempts().first().payload
        self.assertEqual(payload["state"], "complete")
        self.assertEqual(payload["recording"]["url"], RECORDING_URL)
        self.assertEqual([u["transcription"]["transcript"] for u in payload["transcript"]], ["Hello Bob"])

    @mock.patch("bots.tasks.deliver_webhook_task.requests.post")
    def test_sent_with_the_bot_id_and_metadata(self, mock_requests_post, mock_deliver_webhook, mock_url):
        self._use_live_transcription()
        self.bot.metadata = {"customer_id": "abc123"}
        self.bot.save()
        self._create_utterance(self.alice, timestamp_ms=0, transcription={"transcript": "Hello Bob"})
        RecordingManager.set_recording_transcription_in_progress(self.recording)
        self._end_bot()
        mock_requests_post.return_value = mock.Mock(status_code=200, text="ok")

        send_webhook.run(self._transcript_completed_attempts().first().id)

        webhook_body = mock_requests_post.call_args[1]["json"]
        self.assertEqual(webhook_body["trigger"], "transcript.completed")
        self.assertEqual(webhook_body["bot_id"], self.bot.object_id)
        self.assertEqual(webhook_body["bot_metadata"], {"customer_id": "abc123"})
        self.assertEqual(webhook_body["data"]["transcript"][0]["transcription"]["transcript"], "Hello Bob")

    def test_delivered_as_failed_when_an_utterance_failed(self, mock_deliver_webhook, mock_url):
        self._use_live_transcription()
        self._create_utterance(self.alice, timestamp_ms=0, transcription={"transcript": "Hello Bob"})
        self._create_utterance(self.bob, timestamp_ms=2000, failure_data={"reason": TranscriptionFailureReasons.RATE_LIMIT_EXCEEDED})
        RecordingManager.set_recording_transcription_in_progress(self.recording)

        self._end_bot()

        self.assertEqual(self._transcript_completed_attempts().count(), 1)
        payload = self._transcript_completed_attempts().first().payload
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["failure_data"], {"failure_reasons": [TranscriptionFailureReasons.RATE_LIMIT_EXCEEDED]})
        self.assertEqual([u["transcription"]["transcript"] for u in payload["transcript"]], ["Hello Bob"])

    def test_transcription_that_failed_during_the_meeting_is_delivered_when_the_bot_ends(self, mock_deliver_webhook, mock_url):
        self._use_live_transcription()
        RecordingManager.set_recording_transcription_in_progress(self.recording)
        RecordingManager.set_recording_transcription_failed(self.recording, failure_data={"failure_reasons": [TranscriptionFailureReasons.CREDENTIALS_INVALID]})

        # The recording file isn't available until the bot ends
        self.assertEqual(self._transcript_completed_attempts().count(), 0)

        self._end_bot()

        self.assertEqual(self._transcript_completed_attempts().count(), 1)
        payload = self._transcript_completed_attempts().first().payload
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["recording"]["url"], RECORDING_URL)

    def test_not_delivered_when_nothing_was_transcribed(self, mock_deliver_webhook, mock_url):
        self._use_live_transcription()

        self._end_bot()

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.state, RecordingStates.FAILED)
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.NOT_STARTED)
        self.assertEqual(self._transcript_completed_attempts().count(), 0)


def elevenlabs_transcript_response(text):
    return elevenlabs_response(body={"text": text, "language_code": "eng", "language_probability": 0.9, "words": [{"text": text, "start": 0.1, "end": 0.5, "type": "word"}]})


# Live utterances that were still being transcribed when the bot gave up waiting for them and ended
@mock.patch("bots.models.Recording.url", new_callable=mock.PropertyMock, return_value=RECORDING_URL)
@mock.patch("bots.tasks.deliver_webhook_task.deliver_webhook")
@mock.patch("bots.tasks.process_utterance_task.pcm_to_mp3", return_value=b"fake-mp3-data")
@mock.patch("bots.transcription_utils.requests.post")
class TestTranscriptCompletedWebhookForLateUtterances(TranscriptCompletedWebhookTestCase):
    def setUp(self):
        super().setUp()
        self._use_live_transcription()

    def test_transcription_is_completed_when_a_late_utterance_finishes(self, mock_post, mock_pcm_to_mp3, mock_deliver_webhook, mock_url):
        mock_post.return_value = elevenlabs_transcript_response("Hello Bob")
        on_time_utterance = self._create_live_utterance(self.alice, timestamp_ms=0)
        process_utterance.delay(on_time_utterance.id)
        late_utterance = self._create_live_utterance(self.bob, timestamp_ms=2000)

        self._end_bot()

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.FAILED)
        self.assertEqual(self.recording.transcription_failure_data, {"failure_reasons": [TranscriptionFailureReasons.UTTERANCES_STILL_IN_PROGRESS_WHEN_RECORDING_TERMINATED]})
        self.assertEqual(self._transcript_completed_attempts().count(), 1)
        self.assertEqual(self._transcript_completed_attempts().first().payload["state"], "failed")

        mock_post.return_value = elevenlabs_transcript_response("Hi Alice")
        process_utterance.delay(late_utterance.id)

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.COMPLETE)
        self.assertIsNone(self.recording.transcription_failure_data)
        self.assertEqual(self._transcript_completed_attempts().count(), 2)
        payload = self._transcript_completed_attempts().order_by("id").last().payload
        self.assertEqual(payload["state"], "complete")
        self.assertIsNone(payload["failure_data"])
        self.assertEqual(payload["recording"]["url"], RECORDING_URL)
        self.assertEqual([u["transcription"]["transcript"] for u in payload["transcript"]], ["Hello Bob", "Hi Alice"])

    def test_transcription_fails_with_the_real_reason_when_a_late_utterance_fails(self, mock_post, mock_pcm_to_mp3, mock_deliver_webhook, mock_url):
        late_utterance = self._create_live_utterance(self.alice, timestamp_ms=0)
        self._end_bot()

        mock_post.return_value = elevenlabs_response(status_code=401)
        process_utterance.delay(late_utterance.id)

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.FAILED)
        self.assertEqual(self.recording.transcription_failure_data, {"failure_reasons": [TranscriptionFailureReasons.CREDENTIALS_INVALID]})
        self.assertEqual(self._transcript_completed_attempts().count(), 2)
        payload = self._transcript_completed_attempts().order_by("id").last().payload
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["failure_data"], {"failure_reasons": [TranscriptionFailureReasons.CREDENTIALS_INVALID]})

    def test_transcription_waits_for_every_late_utterance(self, mock_post, mock_pcm_to_mp3, mock_deliver_webhook, mock_url):
        first_late_utterance = self._create_live_utterance(self.alice, timestamp_ms=0)
        second_late_utterance = self._create_live_utterance(self.bob, timestamp_ms=2000)
        self._end_bot()

        mock_post.return_value = elevenlabs_transcript_response("Hello Bob")
        process_utterance.delay(first_late_utterance.id)

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.FAILED)
        self.assertEqual(self._transcript_completed_attempts().count(), 1)

        mock_post.return_value = elevenlabs_transcript_response("Hi Alice")
        process_utterance.delay(second_late_utterance.id)

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.COMPLETE)
        self.assertEqual(self._transcript_completed_attempts().count(), 2)

    def test_transcription_that_failed_for_another_reason_is_not_reopened(self, mock_post, mock_pcm_to_mp3, mock_deliver_webhook, mock_url):
        RecordingManager.set_recording_transcription_in_progress(self.recording)
        RecordingManager.set_recording_transcription_failed(self.recording, failure_data={"failure_reasons": [TranscriptionFailureReasons.CREDENTIALS_INVALID]})
        self._end_bot()

        RecordingManager.finish_recording_transcription_if_no_utterances_in_progress(self.recording)

        self.recording.refresh_from_db()
        self.assertEqual(self.recording.transcription_state, RecordingTranscriptionStates.FAILED)
        self.assertEqual(self.recording.transcription_failure_data, {"failure_reasons": [TranscriptionFailureReasons.CREDENTIALS_INVALID]})
        self.assertEqual(self._transcript_completed_attempts().count(), 1)


@mock.patch("bots.models.Recording.url", new_callable=mock.PropertyMock, return_value=RECORDING_URL)
@mock.patch("bots.tasks.deliver_webhook_task.deliver_webhook")
@mock.patch("bots.transcription_utils.get_mp3_for_utterance_group", return_value=b"fake-mp3-data")
@mock.patch("bots.transcription_utils.requests.post")
class TestTranscriptCompletedWebhookForPostMeetingTranscription(TranscriptCompletedWebhookTestCase):
    def test_delivered_after_the_post_meeting_transcription_finishes(self, mock_post, mock_get_mp3, mock_deliver_webhook, mock_url):
        self._create_audio_chunk(self.alice, timestamp_ms=0)
        mock_post.return_value = elevenlabs_response(body={"text": "Hello", "language_code": "eng", "words": [{"text": "Hello", "start": 0.1, "end": 0.5, "type": "word"}]})

        self._end_bot()

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(self._transcript_completed_attempts().count(), 1)
        payload = self._transcript_completed_attempts().first().payload
        self.assertEqual(payload["state"], "complete")
        self.assertEqual(payload["recording"]["url"], RECORDING_URL)
        self.assertEqual([u["transcription"]["transcript"] for u in payload["transcript"]], ["Hello"])

    def test_delivered_as_failed_when_the_post_meeting_transcription_fails(self, mock_post, mock_get_mp3, mock_deliver_webhook, mock_url):
        self._create_audio_chunk(self.alice, timestamp_ms=0)
        mock_post.return_value = elevenlabs_response(status_code=401)

        self._end_bot()

        self.assertEqual(self._transcript_completed_attempts().count(), 1)
        payload = self._transcript_completed_attempts().first().payload
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["failure_data"], {"failure_reasons": [TranscriptionFailureReasons.CREDENTIALS_INVALID]})
        self.assertEqual(payload["transcript"], [])

    def test_delivered_once_when_finishing_is_attempted_more_than_once(self, mock_post, mock_get_mp3, mock_deliver_webhook, mock_url):
        self._create_audio_chunk(self.alice, timestamp_ms=0)
        mock_post.return_value = elevenlabs_response(body={"text": "Hello", "language_code": "eng", "words": [{"text": "Hello", "start": 0.1, "end": 0.5, "type": "word"}]})

        self._end_bot()
        RecordingManager.finish_recording_transcription_if_no_utterances_in_progress(self.recording)

        self.assertEqual(self._transcript_completed_attempts().count(), 1)


class TestTranscriptCompletedTriggerType(SimpleTestCase):
    def test_api_code(self):
        self.assertEqual(WebhookTriggerTypes.trigger_type_to_api_code(WebhookTriggerTypes.TRANSCRIPT_COMPLETED), "transcript.completed")
        self.assertEqual(WebhookTriggerTypes.api_code_to_trigger_type("transcript.completed"), WebhookTriggerTypes.TRANSCRIPT_COMPLETED)
