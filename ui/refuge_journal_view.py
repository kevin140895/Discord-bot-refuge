from __future__ import annotations

import discord

from services.refuge_journal import JournalLeader, RefugeJournalIssue


def _format_voice(seconds: int) -> str:
    minutes = max(0, int(seconds)) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _format_leader(leader: JournalLeader | None, metric: str) -> str:
    if leader is None:
        return "—"
    if metric == "xp":
        value = f"+{leader.value} XP"
    elif metric == "messages":
        value = f"{leader.value} messages"
    elif metric == "voice":
        value = _format_voice(leader.value)
    else:
        value = str(leader.value)
    return f"<@{leader.user_id}> · **{value}**"


def _achievement_text(issue: RefugeJournalIssue) -> str:
    if issue.achievement_count <= 0:
        return "Aucun nouveau succès enregistré sur la période."
    lines = [f"**{issue.achievement_count}** nouveau(x) succès débloqué(s)."]
    for achievement in issue.achievement_highlights:
        lines.append(
            f"{achievement.emoji} <@{achievement.user_id}> — **{achievement.name}**"
        )
    return "\n".join(lines)


def _game_text(issue: RefugeJournalIssue) -> str:
    if issue.game_event_count <= 0:
        return "Aucun événement gaming terminé sur la période."
    games = " · ".join(issue.game_names) if issue.game_names else "Jeux non précisés"
    return (
        f"**{issue.game_event_count}** événement(s) terminé(s) · "
        f"**{issue.game_participations}** participation(s)\n"
        f"🎮 {games}"
    )


def _refuge_text(issue: RefugeJournalIssue) -> str:
    if issue.refuge_event_count <= 0:
        return "Aucun événement historique du Refuge enregistré sur la période."
    lines = [f"**{issue.refuge_event_count}** événement(s) ont marqué le Refuge."]
    lines.extend(f"• {label}" for label in issue.refuge_event_labels)
    return "\n".join(lines)


class RefugeJournalView(discord.ui.LayoutView):
    """Static Components V2 newspaper generated from authoritative bot data."""

    def __init__(self, issue: RefugeJournalIssue, *, preview: bool = False) -> None:
        super().__init__(timeout=None)

        start_ts = int(issue.period_start.timestamp())
        end_ts = int(issue.period_end.timestamp())
        title_suffix = " · APERÇU" if preview else ""

        container = discord.ui.Container(accent_colour=discord.Colour.blurple())
        container.add_item(
            discord.ui.TextDisplay(
                f"# 📰 Journal du Refuge — #{issue.issue_number}{title_suffix}\n"
                f"Du <t:{start_ts}:d> au <t:{end_ts}:d> · "
                f"Référence `{issue.publication_key}`"
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "## 📊 Cette période au Refuge\n"
                f"⚡ **{issue.total_xp:+d} XP** gagnée · "
                f"💬 **{issue.total_messages}** messages · "
                f"🎙️ **{_format_voice(issue.total_voice_seconds)}** en vocal\n"
                f"🎰 **{issue.casino_bets}** paris · bilan casino **{issue.casino_net:+d} XP net**"
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "## 🏆 Membres de la période\n"
                f"⚡ XP — {_format_leader(issue.xp_leader, 'xp')}\n"
                f"💬 Messages — {_format_leader(issue.messages_leader, 'messages')}\n"
                f"🎙️ Vocal — {_format_leader(issue.voice_leader, 'voice')}"
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "## 🎖️ Succès débloqués\n" + _achievement_text(issue)
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "## 🎮 Événements & soirées\n" + _game_text(issue)
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "## 🏕️ Chronique du Refuge\n" + _refuge_text(issue)
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "-# Données générées automatiquement à partir de l’activité réelle du bot. "
                f"Référence de publication : `{issue.publication_key}`."
            )
        )
        self.add_item(container)


__all__ = ["RefugeJournalView"]
