from types import SimpleNamespace
from unittest.mock import patch

import discord

import cogs.temp_vc as temp_vc


def test_game_name_has_priority_over_sleep_and_chat():
    channel = SimpleNamespace(members=[])
    bot = SimpleNamespace(get_channel=lambda _id: None)

    # Empêche le démarrage de la boucle de nettoyage lors de l'instanciation
    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    cog._base_name_from_members = lambda members: "Base"

    player = SimpleNamespace(
        activities=[discord.Game("Minecraft")],
        voice=SimpleNamespace(self_mute=False),
    )
    sleeper = SimpleNamespace(
        activities=[],
        voice=SimpleNamespace(self_mute=True),
    )

    channel.members = [sleeper, player]

    assert cog._compute_channel_name(channel) == "Base • Minecraft"


def test_most_common_game_name_used():
    channel = SimpleNamespace(members=[])
    bot = SimpleNamespace(get_channel=lambda _id: None)

    with patch.object(temp_vc.tasks.Loop, "start", lambda self, *a, **k: None):
        cog = temp_vc.TempVCCog(bot)

    cog._base_name_from_members = lambda members: "Base"

    player1 = SimpleNamespace(
        activities=[discord.Game("Minecraft")],
        voice=SimpleNamespace(self_mute=False),
    )
    player2 = SimpleNamespace(
        activities=[discord.Game("Minecraft")],
        voice=SimpleNamespace(self_mute=False),
    )
    player3 = SimpleNamespace(
        activities=[discord.Game("Fortnite")],
        voice=SimpleNamespace(self_mute=False),
    )

    channel.members = [player1, player2, player3]

    assert cog._compute_channel_name(channel) == "Base • Minecraft"

