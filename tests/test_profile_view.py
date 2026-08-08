from types import SimpleNamespace

import discord
import pytest

from services.member_profile import MemberProfileSnapshot
from ui.profile_view import (
    ProfileNavigationButton,
    ProfileView,
    _format_rank,
    _format_signed_xp,
    _format_voice,
    _format_xp,
)


def _snapshot() -> MemberProfileSnapshot:
    return MemberProfileSnapshot(
        user_id=42,
        xp=12450,
        level=18,
        achievements_unlocked=6,
        achievements_total=9,
        achievement_ids=(
            "casino_1_bet",
            "casino_10_bets",
            "level_5",
            "level_10",
            "tenure_30_days",
            "tenure_180_days",
        ),
        season_id="2026-08",
        season_xp=3210,
        season_xp_rank=4,
        season_messages=1482,
        season_messages_rank=7,
        season_voice_seconds=98040,
        season_voice_rank=2,
        season_casino_net=250,
        season_casino_rank=5,
        casino_bets=48,
        casino_wagered=9000,
        casino_winnings=10320,
        casino_net=1320,
    )


def _container_text(view: ProfileView) -> str:
    container = view.children[0]
    return "\n".join(
        item.content
        for item in container.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


def test_profile_formatters_are_mobile_compact() -> None:
    assert _format_rank(4) == "#4"
    assert _format_rank(None) == "—"
    assert _format_voice(98040) == "27h 14m"
    assert _format_xp(12450) == "12 450 XP"
    assert _format_signed_xp(1320) == "+1320 XP"
    assert _format_signed_xp(-50) == "-50 XP"


def test_profile_overview_uses_active_components_v2_navigation() -> None:
    view = ProfileView(
        _snapshot(),
        display_name="Kevin",
        avatar_url="https://cdn.discordapp.com/embed/avatars/0.png",
        owner_user_id=99,
    )

    assert isinstance(view, discord.ui.LayoutView)
    assert view.current_page == "overview"
    assert view.owner_user_id == 99
    assert len(view.children) == 1

    container = view.children[0]
    assert isinstance(container, discord.ui.Container)
    assert isinstance(container.children[0], discord.ui.Section)
    assert isinstance(container.children[0].accessory, discord.ui.Thumbnail)

    text = _container_text(view)
    assert "PROFIL DU REFUGE" in text
    assert "Kevin" in text
    assert "Niveau **18**" in text
    assert "Août 2026" in text
    assert "**6/9** badges" in text
    assert "**48** paris" in text

    row = container.children[-1]
    assert isinstance(row, discord.ui.ActionRow)
    assert [button.label for button in row.children] == ["Succès", "Saison", "Casino"]
    assert all(isinstance(button, ProfileNavigationButton) for button in row.children)
    assert all(not button.disabled for button in row.children)


def test_profile_pages_replace_content_and_offer_one_back_button() -> None:
    view = ProfileView(_snapshot(), display_name="Kevin")

    view.show_page("achievements")
    assert view.current_page == "achievements"
    text = _container_text(view)
    assert "SUCCÈS" in text
    assert "Membre Bronze" in text
    assert "Membre Or" in text
    assert "✅" in text
    assert "🔒" in text

    row = view.children[0].children[-1]
    assert [button.label for button in row.children] == ["Retour au profil"]

    view.show_page("season")
    text = _container_text(view)
    assert "SAISON · Août 2026" in text
    assert "3 210 XP" in text
    assert "rang **#4**" in text
    assert "27h 14m" in text

    view.show_page("casino")
    text = _container_text(view)
    assert "CASINO" in text
    assert "Mises : **9 000 XP**" in text
    assert "Gains : **10 320 XP**" in text
    assert "Résultat net : **+1320 XP**" in text

    view.show_page("overview")
    assert view.current_page == "overview"
    assert "PROFIL DU REFUGE" in _container_text(view)


def test_profile_view_without_avatar_keeps_identity_visible() -> None:
    view = ProfileView(_snapshot(), display_name="Kevin")
    container = view.children[0]

    assert isinstance(container, discord.ui.Container)
    assert isinstance(container.children[0], discord.ui.TextDisplay)
    assert "Kevin" in container.children[0].content


def test_profile_view_rejects_unknown_page() -> None:
    view = ProfileView(_snapshot(), display_name="Kevin")
    with pytest.raises(ValueError):
        view.show_page("unknown")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_profile_navigation_is_restricted_to_command_author() -> None:
    class FakeResponse:
        def __init__(self) -> None:
            self.messages: list[tuple[str, bool]] = []

        async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
            self.messages.append((content, ephemeral))

    view = ProfileView(_snapshot(), display_name="Kevin", owner_user_id=99)

    owner_response = FakeResponse()
    owner_interaction = SimpleNamespace(
        user=SimpleNamespace(id=99),
        response=owner_response,
    )
    assert await view.interaction_check(owner_interaction) is True
    assert owner_response.messages == []

    other_response = FakeResponse()
    other_interaction = SimpleNamespace(
        user=SimpleNamespace(id=100),
        response=other_response,
    )
    assert await view.interaction_check(other_interaction) is False
    assert other_response.messages == [
        ("Ce panneau est contrôlé par la personne qui a lancé `/profil`.", True)
    ]
