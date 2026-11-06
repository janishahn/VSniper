from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from vsniper.domain.contracts import (
    CandidateRecord,
    SearchDraftApplyResult,
    TasteRecomputeResult,
    TasteSnapshot,
    TelegramFeedbackCallback,
    TelegramTasteCallback,
)


CALLBACK_PREFIX = "feedback"
TASTE_CALLBACK_PREFIX = "taste"


_DESCRIPTION_MAX_CHARS = 300
_LOCAL_TZ = ZoneInfo("Europe/Berlin")
_DECISION_LABELS = {"alert": "Treffer", "review": "Knapp daneben", "discard": "Verworfen"}
# Marks where the feedback badge starts, so a second vote replaces it instead of stacking.
_FEEDBACK_MARKER = "\n\nDeine Bewertung:"


# Telegram captions hold 1024 characters; the vote badge added later needs the rest.
ALERT_CAPTION_MAX_CHARS = 900


class TelegramFormatter:
    def build_alert_caption(self, candidate: CandidateRecord, kind: str = "alert") -> str:
        """The alert text short enough for a photo caption: the description goes first, then the
        explanation is cut."""
        text = self.build_alert_message(candidate, kind)
        if len(text) <= ALERT_CAPTION_MAX_CHARS:
            return text
        text = self.build_alert_message(candidate, kind, include_description=False)
        if len(text) <= ALERT_CAPTION_MAX_CHARS:
            return text
        body, _, link = text.rpartition("\n")
        return body[: ALERT_CAPTION_MAX_CHARS - len(link) - 2].rstrip() + "…\n" + link

    def build_alert_message(self, candidate: CandidateRecord, kind: str = "alert", *, include_description: bool = True) -> str:
        description = (candidate.description or "").strip() if include_description else ""
        if len(description) > _DESCRIPTION_MAX_CHARS:
            boundary = description.rfind(" ", 0, _DESCRIPTION_MAX_CHARS)
            description = description[: boundary if boundary > 50 else _DESCRIPTION_MAX_CHARS].rstrip() + "…"

        trace = candidate.score_trace
        parts = (
            [
                f"🤔 Knapp daneben: {trace.score_10}/100, knapp unter der Alarmschwelle von "
                f"{round(trace.threshold * 100)}. Gefällt dir so etwas?\n"
            ]
            if kind == "borderline"
            else []
        )
        parts += [
            f"🧥 {candidate.title}",
            f"Marke: {candidate.brand}  |  Größe: {candidate.size}  |  Preis: {candidate.price_eur:.2f} €",
        ]
        if description:
            parts.append(f"\n{description}")
        if kind != "borderline":
            parts += [
                f"\nBewertung: {_DECISION_LABELS.get(trace.decision, trace.decision)} ({trace.score_10}/100)",
                f"Warum: {trace.explanation or trace.summary}",
            ]
        parts.append(f"🔗 {candidate.url}")
        return "\n".join(parts)

    def build_feedback_reply_markup(self, delivery_id: str) -> dict[str, list[list[dict[str, str]]]]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "👍 Gefällt mir",
                        "callback_data": self.build_feedback_callback_data(delivery_id=delivery_id, verdict="like"),
                    },
                    {
                        "text": "👎 Gefällt mir nicht",
                        "callback_data": self.build_feedback_callback_data(delivery_id=delivery_id, verdict="dislike"),
                    },
                ]
            ]
        }

    @staticmethod
    def build_feedback_applied_message(*, message_text: str, verdict: str) -> str:
        base_text = message_text.split(_FEEDBACK_MARKER, maxsplit=1)[0].rstrip()
        badge = "👍 Gefällt mir" if verdict == "like" else "👎 Gefällt mir nicht"
        return f"{base_text}{_FEEDBACK_MARKER} {badge}\n↩ Antworte auf diese Nachricht, um eine Notiz dazu zu hinterlassen."

    @staticmethod
    def build_feedback_callback_data(*, delivery_id: str, verdict: str) -> str:
        return f"{CALLBACK_PREFIX}:{delivery_id}:{verdict}"

    @staticmethod
    def parse_feedback_callback_data(data: str) -> TelegramFeedbackCallback | None:
        prefix, separator, payload = data.partition(":")
        if prefix != CALLBACK_PREFIX or not separator or not payload:
            return None

        delivery_id, separator, verdict = payload.partition(":")
        if not separator or not delivery_id or verdict not in {"like", "dislike"}:
            return None

        typed_verdict: Literal["like", "dislike"] = "like" if verdict == "like" else "dislike"
        return TelegramFeedbackCallback(delivery_id=delivery_id, verdict=typed_verdict)

    @staticmethod
    def build_taste_callback_data(*, action: str, profile_version: int | None = None) -> str:
        if profile_version is None:
            return f"{TASTE_CALLBACK_PREFIX}:{action}"
        return f"{TASTE_CALLBACK_PREFIX}:{action}:{profile_version}"

    @staticmethod
    def parse_taste_callback_data(data: str) -> TelegramTasteCallback | None:
        parts = data.split(":")
        if len(parts) < 2 or parts[0] != TASTE_CALLBACK_PREFIX:
            return None
        action = parts[1]
        if action not in {"recompute", "apply_drafts", "skip_drafts"}:
            return None
        profile_version: int | None = None
        if len(parts) == 3 and parts[2]:
            try:
                profile_version = int(parts[2])
            except ValueError:
                return None
        elif len(parts) > 3:
            return None
        typed_action: Literal["recompute", "apply_drafts", "skip_drafts"]
        typed_action = "recompute" if action == "recompute" else "apply_drafts" if action == "apply_drafts" else "skip_drafts"
        return TelegramTasteCallback(action=typed_action, profile_version=profile_version)

    def build_taste_status_reply_markup(self) -> dict[str, list[list[dict[str, str]]]]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "🧠 Geschmack neu berechnen",
                        "callback_data": self.build_taste_callback_data(action="recompute"),
                    }
                ]
            ]
        }

    def build_taste_draft_reply_markup(self, *, profile_version: int) -> dict[str, list[list[dict[str, str]]]]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Suchvorschläge übernehmen",
                        "callback_data": self.build_taste_callback_data(
                            action="apply_drafts",
                            profile_version=profile_version,
                        ),
                    },
                    {
                        "text": "Jetzt nicht",
                        "callback_data": self.build_taste_callback_data(
                            action="skip_drafts",
                            profile_version=profile_version,
                        ),
                    },
                ]
            ]
        }

    @staticmethod
    def _format_dt(value: datetime | None) -> str:
        if value is None:
            return "noch nie"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(_LOCAL_TZ).strftime("%d.%m.%Y, %H:%M Uhr")

    def build_taste_status_message(self, snapshot: TasteSnapshot) -> str:
        profile = snapshot.taste_profile
        dirty = snapshot.dirty_counts
        recompute = snapshot.recompute_state
        parts = [
            "🧠 Dein Geschmack",
            f"Beispiele: {len(snapshot.samples)} insgesamt · {dirty.new_or_changed_samples} neu seit der letzten Berechnung",
            (
                "Davon: "
                f"{dirty.new_or_changed_positive_samples} positiv · "
                f"{dirty.new_or_changed_negative_samples} negativ · "
                f"Notiz {'geändert' if dirty.manual_note_changed else 'unverändert'}"
            ),
            f"Zuletzt berechnet: {self._format_dt(snapshot.last_recomputed_at)}",
        ]
        if profile is None:
            parts.append("Profil: noch keins")
        else:
            model_bits = [f"v{profile.version}"]
            if profile.model:
                model_bits.append(profile.model)
            parts.append(f"Profil: {' · '.join(model_bits)}")
            parts.append(f"Suchvorschläge: {len(profile.generated_searches)}")
        if recompute.status == "running":
            parts.append(f"Berechnung läuft seit {self._format_dt(recompute.started_at)}")
        elif recompute.status == "failed" and recompute.error:
            parts.append(f"Letzte Berechnung fehlgeschlagen: {recompute.error[:160]}")
        elif recompute.status == "succeeded":
            parts.append(
                f"Letzte Kosten: ${recompute.last_cost_usd:.4f} "
                f"({recompute.last_input_tokens} Tokens rein / {recompute.last_output_tokens} raus)"
            )
        return "\n".join(parts)

    @staticmethod
    def build_taste_recompute_started_message(*, started_at: datetime | None = None) -> str:
        return (
            f"🧠 Geschmack wird neu berechnet (gestartet {TelegramFormatter._format_dt(started_at)}). "
            "Ich melde mich hier, sobald es fertig ist."
        )

    @staticmethod
    def build_taste_recompute_already_running_message(*, started_at: datetime | None = None) -> str:
        return (
            f"🧠 Die Berechnung läuft schon seit {TelegramFormatter._format_dt(started_at)}. "
            "Ich starte keine zweite."
        )

    def build_taste_recompute_success_message(self, result: TasteRecomputeResult) -> str:
        profile = result.snapshot.taste_profile
        profile_text = f"v{profile.version}" if profile else "unbekannte Version"
        draft_count = len(profile.generated_searches) if profile else 0
        return "\n".join(
            [
                "✅ Geschmack neu berechnet",
                f"Profil: {profile_text}",
                f"Kosten: ${result.cost_usd:.4f}",
                f"Tokens: {result.input_tokens} rein / {result.output_tokens} raus",
                f"Bildbeschreibungen: {result.observation_cache.cached_observations} wiederverwendet / {result.observation_cache.fresh_observations} neu",
                f"Suchvorschläge: {draft_count}",
                "Suchvorschläge übernehmen?" if draft_count else "Es gibt keine Suchvorschläge zum Übernehmen.",
            ]
        )

    @staticmethod
    def build_taste_recompute_failed_message(error: str) -> str:
        return f"❌ Neuberechnung fehlgeschlagen: {error[:800]}"

    @staticmethod
    def build_search_draft_apply_message(result: SearchDraftApplyResult) -> str:
        if result.stale:
            return (
                f"⚠️ Das Profil ist inzwischen bei v{result.profile_version}, dieser Knopf war für "
                f"v{result.requested_profile_version}. Schick /taste, um das aktuelle Profil anzusehen."
            )
        if result.profile_version is None:
            return "Es gibt noch kein Geschmacksprofil und damit keine Suchvorschläge."
        return "\n".join(
            [
                f"✅ Suchvorschläge aus Profil v{result.profile_version} übernommen",
                f"Geändert: {result.applied_searches} · unverändert: {result.unchanged_searches} · übersprungen: {result.skipped_searches}",
            ]
        )
