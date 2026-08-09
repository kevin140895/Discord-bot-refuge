from __future__ import annotations

from datetime import datetime, timezone

import discord

from services.refuge_construction import (
    CONSTRUCTION_STATUS_BUILDING,
    CONSTRUCTION_STATUS_TIE_BREAK,
    CONSTRUCTION_STATUS_VOTING,
    PROJECT_BY_ID,
    RefugeConstructionSnapshot,
    refuge_construction_service,
)


REFUGE_CONSTRUCTION_ACCENT = discord.Colour(0xB77B42)


def _discord_time(value: str | None, style: str = "R") -> str:
    if not value:
        return "échéance inconnue"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "échéance inconnue"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return f"<t:{int(parsed.timestamp())}:{style}>"


def _progress_bar(percent: int, width: int = 10) -> str:
    normalized = max(0, min(100, int(percent)))
    filled = min(width, max(0, int(normalized * width / 100)))
    return "█" * filled + "░" * (width - filled)


def _winner_method_label(method: str | None) -> str:
    return {
        "vote": "majorité au vote",
        "tie_extension_vote": "majorité après prolongation",
        "random_tie": "tirage au sort après égalité persistante",
    }.get(str(method or ""), "résultat du vote")


class RefugeConstructionVoteSelect(discord.ui.Select):
    def __init__(self, snapshot: RefugeConstructionSnapshot) -> None:
        option_by_id = {option.project_id: option for option in snapshot.options}
        options: list[discord.SelectOption] = []
        for project_id in snapshot.allowed_project_ids:
            project = option_by_id.get(project_id)
            if project is None:
                continue
            options.append(
                discord.SelectOption(
                    label=project.name,
                    value=project.project_id,
                    emoji=project.emoji,
                    description=project.description[:100] or None,
                    default=snapshot.user_vote == project.project_id,
                )
            )
        super().__init__(
            custom_id="refuge:construction:vote",
            placeholder="Choisir le projet du Refuge",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, RefugeConstructionView):
            return
        await interaction.response.defer()
        try:
            snapshot = await refuge_construction_service.cast_vote(
                interaction.user.id,
                self.values[0],
            )
        except ValueError:
            snapshot = await refuge_construction_service.get_snapshot(interaction.user.id)
        view.refresh(snapshot)
        await interaction.edit_original_response(view=view)


class RefugeConstructionView(discord.ui.LayoutView):
    """Private construction vote/status surface. Live vote totals stay hidden."""

    def __init__(
        self,
        snapshot: RefugeConstructionSnapshot,
        *,
        owner_user_id: int,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_user_id = int(owner_user_id)
        self.snapshot = snapshot
        self.refresh(snapshot)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id == self.owner_user_id:
            return True
        await interaction.response.send_message(
            "Ce bulletin privé appartient à la personne qui l’a ouvert.",
            ephemeral=True,
        )
        return False

    def refresh(self, snapshot: RefugeConstructionSnapshot) -> None:
        self.snapshot = snapshot
        self.clear_items()
        container = discord.ui.Container(accent_colour=REFUGE_CONSTRUCTION_ACCENT)
        container.add_item(
            discord.ui.TextDisplay(
                "## 🏗️ LE CHANTIER\n"
                "Chaque droit de bâtir vient d’un objectif communautaire réellement réussi."
            )
        )
        container.add_item(discord.ui.Separator())

        if not snapshot.active:
            lines = [
                "### Aucun chantier actif",
                "Le prochain vote s’ouvrira lorsqu’un nouvel objectif communautaire sera validé.",
                "Aucune action répétitive ne peut accélérer l’ouverture d’un chantier.",
            ]
            container.add_item(discord.ui.TextDisplay("\n".join(lines)))
        elif snapshot.status in {CONSTRUCTION_STATUS_VOTING, CONSTRUCTION_STATUS_TIE_BREAK}:
            self._add_vote(container, snapshot)
        elif snapshot.status == CONSTRUCTION_STATUS_BUILDING:
            self._add_building(container, snapshot)
        else:
            container.add_item(
                discord.ui.TextDisplay(
                    "Le chantier existe, mais son état actuel n’est pas encore affichable."
                )
            )

        if snapshot.completed_monuments:
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "### 🗿 Constructions permanentes\n"
                    + "\n".join(f"• {name}" for name in snapshot.completed_monuments)
                )
            )

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "-# Un membre = un vote. Tu peux modifier ton choix tant que le scrutin reste ouvert."
            )
        )
        self.add_item(container)

    def _add_vote(
        self,
        container: discord.ui.Container,
        snapshot: RefugeConstructionSnapshot,
    ) -> None:
        if snapshot.status == CONSTRUCTION_STATUS_TIE_BREAK:
            heading = "### ⚖️ Prolongation pour égalité"
            intro = (
                "Le premier scrutin s’est terminé à égalité. "
                "Seuls les projets encore ex æquo restent éligibles pendant 24 h."
            )
        else:
            heading = "### 🗳️ Vote communautaire"
            intro = "Choisis le projet qui deviendra une construction permanente du Refuge."

        source = snapshot.source_goal_title or "Objectif communautaire réussi"
        lines = [
            heading,
            f"Origine : **{source}**",
            f"Clôture : **{_discord_time(snapshot.closes_at)}**",
            intro,
            "",
        ]
        allowed = set(snapshot.allowed_project_ids)
        for option in snapshot.options:
            if option.project_id not in allowed:
                continue
            lines.append(f"{option.emoji} **{option.name}** — {option.description}")
        lines.extend(
            [
                "",
                (
                    f"Ton vote actuel : **{PROJECT_BY_ID[snapshot.user_vote].name}**"
                    if snapshot.user_vote in PROJECT_BY_ID
                    else "Ton vote actuel : **aucun**"
                ),
                "🔒 Les résultats intermédiaires restent masqués jusqu’à la clôture.",
            ]
        )
        container.add_item(discord.ui.TextDisplay("\n".join(lines)))
        if snapshot.allowed_project_ids:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(RefugeConstructionVoteSelect(snapshot)))

    def _add_building(
        self,
        container: discord.ui.Container,
        snapshot: RefugeConstructionSnapshot,
    ) -> None:
        project_name = snapshot.project_name or "Projet du Refuge"
        percent = max(0, min(100, int(snapshot.progress_percent)))
        lines = [
            f"### 🧱 {project_name}",
            (
                f"{_progress_bar(percent)} **{percent}%**\n"
                f"Inauguration : **{_discord_time(snapshot.completes_at)}**"
            ),
            "La progression dépend uniquement du temps écoulé.",
            "",
            f"Résolution du vote : **{_winner_method_label(snapshot.winner_method)}**.",
        ]

        if snapshot.final_results:
            option_by_id = {option.project_id: option for option in snapshot.options}
            lines.append("Résultats finaux :")
            for project_id, count in snapshot.final_results:
                option = option_by_id.get(project_id)
                name = option.name if option is not None else project_id
                lines.append(f"• {name} : **{count}**")
        container.add_item(discord.ui.TextDisplay("\n".join(lines)))


__all__ = [
    "REFUGE_CONSTRUCTION_ACCENT",
    "RefugeConstructionView",
    "RefugeConstructionVoteSelect",
]
