import discord

from services.member_profile import MemberProfileSnapshot
from ui.profile_view import (
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
        achievement_ids=("level_5", "level_10"),
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


def test_profile_formatters_are_mobile_compact() -> None:
    assert _format_rank(4) == "#4"
    assert _format_rank(None) == "—"
    assert _format_voice(98040) == "27h 14m"
    assert _format_xp(12450) == "12 450 XP"
    assert _format_signed_xp(1320) == "+1320 XP"
    assert _format_signed_xp(-50) == "-50 XP"


def test_profile_view_uses_components_v2_with_avatar() -> None:
    view = ProfileView(
        _snapshot(),
        display_name="Kevin",
        avatar_url="https://cdn.discordapp.com/embed/avatars/0.png",
    )

    assert isinstance(view, discord.ui.LayoutView)
    assert len(view.children) == 1

    container = view.children[0]
    assert isinstance(container, discord.ui.Container)
    assert isinstance(container.children[0], discord.ui.Section)
    assert isinstance(container.children[0].accessory, discord.ui.Thumbnail)

    text = "\n".join(
        item.content
        for item in container.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert "PROFIL DU REFUGE" in text
    assert "Kevin" in text
    assert "Niveau **18**" in text
    assert "Août 2026" in text
    assert "**6/9** badges" in text
    assert "**48** paris" in text

    row = container.children[-1]
    assert isinstance(row, discord.ui.ActionRow)
    assert [button.label for button in row.children] == ["Succès", "Saison", "Casino"]
    assert all(button.disabled for button in row.children)


def test_profile_view_without_avatar_keeps_identity_visible() -> None:
    view = ProfileView(_snapshot(), display_name="Kevin")
    container = view.children[0]

    assert isinstance(container, discord.ui.Container)
    assert isinstance(container.children[0], discord.ui.TextDisplay)
    assert "Kevin" in container.children[0].content
