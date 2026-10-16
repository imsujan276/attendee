from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Organization, User, UserRole
from bots.models import AudioChunk, Bot, BotStates, CreditTransaction, CreditTransactionManager, Participant, Project, Recording, RecordingStates, Utterance


class TestDashboardBotDeletion(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Test Organization")
        self.user = User.objects.create_user(username="admin", email="admin@example.com", password="testpassword123", role=UserRole.ADMIN, organization=self.organization)
        self.project = Project.objects.create(name="Test Project", organization=self.organization)
        self.client = Client()
        self.client.force_login(self.user)

    def create_bot(self, state=BotStates.ENDED, project=None):
        bot = Bot.objects.create(project=project or self.project, meeting_url="https://meet.google.com/abc-defg-hij", state=state)
        recording = Recording.objects.create(bot=bot, recording_type=1, transcription_type=1, state=RecordingStates.COMPLETE, is_default_recording=True)
        recording.file.save(f"{bot.object_id}.mp4", ContentFile(b"recording"))
        participant = Participant.objects.create(bot=bot, uuid=f"{bot.object_id}-participant")
        audio_chunk = AudioChunk.objects.create(recording=recording, participant=participant, audio_blob=b"audio", timestamp_ms=0, duration_ms=500, sample_rate=16000)
        Utterance.objects.create(recording=recording, participant=participant, audio_chunk=audio_chunk, timestamp_ms=0, duration_ms=500)
        return bot

    def delete_bot(self, bot, mode):
        return self.client.post(reverse("projects:delete-bot", kwargs={"object_id": self.project.object_id, "bot_object_id": bot.object_id}), {"mode": mode})

    def bulk_delete(self, bots, mode, next_url=None):
        data = {"bot_ids": [bot.object_id for bot in bots], "mode": mode}
        if next_url:
            data["next"] = next_url
        return self.client.post(reverse("projects:bulk-delete-bots", kwargs={"object_id": self.project.object_id}), data)

    def message_texts(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_delete_data_keeps_bot(self):
        bot = self.create_bot()

        response = self.delete_bot(bot, "data")

        self.assertRedirects(response, reverse("projects:project-bot-detail", kwargs={"object_id": self.project.object_id, "bot_object_id": bot.object_id}), fetch_redirect_response=False)
        bot.refresh_from_db()
        self.assertEqual(bot.state, BotStates.DATA_DELETED)
        self.assertFalse(bot.recordings.get().file)
        self.assertFalse(bot.participants.exists())
        self.assertIn("Deleted the data of 1 bot(s).", self.message_texts(response))

    def test_remove_deletes_bot_and_keeps_credit_transactions(self):
        bot = self.create_bot()
        CreditTransactionManager.create_transaction(organization=self.organization, centicredits_delta=-100, bot=bot, description="Test transaction")

        response = self.delete_bot(bot, "remove")

        self.assertRedirects(response, reverse("projects:project-bots", kwargs={"object_id": self.project.object_id}), fetch_redirect_response=False)
        self.assertFalse(Bot.objects.filter(id=bot.id).exists())
        self.assertFalse(Recording.objects.filter(bot_id=bot.id).exists())
        self.assertEqual(CreditTransaction.objects.filter(description="Test transaction", bot__isnull=True).count(), 1)

    def test_running_bot_is_not_deleted(self):
        bot = self.create_bot(state=BotStates.JOINED_RECORDING)

        response = self.delete_bot(bot, "remove")

        bot.refresh_from_db()
        self.assertEqual(bot.state, BotStates.JOINED_RECORDING)
        self.assertTrue(bot.recordings.get().file)
        self.assertIn("Skipped 1 that are still running.", self.message_texts(response))

    def test_bulk_delete_data_skips_running_bots_and_returns_to_list(self):
        ended_bot = self.create_bot(state=BotStates.ENDED)
        failed_bot = self.create_bot(state=BotStates.FATAL_ERROR)
        running_bot = self.create_bot(state=BotStates.JOINED_RECORDING)
        next_url = reverse("projects:project-bots", kwargs={"object_id": self.project.object_id}) + "?page=2&search=meet"

        response = self.bulk_delete([ended_bot, failed_bot, running_bot], "data", next_url=next_url)

        self.assertRedirects(response, next_url, fetch_redirect_response=False)
        for bot in [ended_bot, failed_bot]:
            bot.refresh_from_db()
            self.assertEqual(bot.state, BotStates.DATA_DELETED)
        running_bot.refresh_from_db()
        self.assertEqual(running_bot.state, BotStates.JOINED_RECORDING)
        self.assertIn("Deleted the data of 2 bot(s). Skipped 1 that are still running or already have their data deleted.", self.message_texts(response))

    def test_bulk_remove_ignores_bots_from_other_projects(self):
        other_project = Project.objects.create(name="Other Project", organization=self.organization)
        bot = self.create_bot()
        other_bot = self.create_bot(project=other_project)

        self.bulk_delete([bot, other_bot], "remove")

        self.assertFalse(Bot.objects.filter(id=bot.id).exists())
        self.assertTrue(Bot.objects.filter(id=other_bot.id).exists())

    def test_bulk_delete_does_not_redirect_off_site(self):
        bot = self.create_bot()

        response = self.bulk_delete([bot], "data", next_url="https://example.com/")

        self.assertRedirects(response, reverse("projects:project-bots", kwargs={"object_id": self.project.object_id}), fetch_redirect_response=False)
