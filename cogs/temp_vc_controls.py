from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict

import discord
from discord import app_commands
from discord.ext import commands

from config import LOBBY_VC_ID
from cogs.temp_vc import TEMP_VC_IDS
from storage.temp_vc_control_store import (
    load_temp_vc_owners,
    save_temp_vc_owners_async,
)

logger = logging.getLogger(__name__)

MemberAction = Callable[[discord.Interaction, discord.Member], Awaitable[None]]


def build_control_embed() -> discord.Embed:
    """Return the generic control panel without touching dynamic channel naming."""
    embed = discord.Embed(
        title="🎛️ Contrôles du salon vocal",
        description=(
            "Ces contrôles s'appliquent au salon vocal temporaire dans lequel tu te trouves.\n"
            "Le nom du salon reste géré automatiquement par le Refuge "
            "(plateforme, Crossplay, activité et AFK)."
        ),
    )
    embed.add_field(
        name="Accès",
        value="🔒 Verrouiller · 👁️ Masquer · ✅ Autoriser · 🚫 Bloquer",
        inline=False,
    )
    embed.add_field(
        name="Gestion",
        value="👑 Claim · 🔁 Transférer · 👥 Limite · 👢 Expulser",
        inline=False,
    )
    return embed


class LimitModal(discord.ui.Modal, title="Limite du salon vocal"):
    limit = discord.ui.TextInput(
        label="Nombre maximum de membres",
        placeholder="0 = aucune limite, sinon 1 à 99",
        min_length=1,
        max_length=2,
        required=True,
    )

    def __init__(self, cog: "TempVCControlsCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.set_user_limit(interaction, str(self.limit.value))


class MemberPicker(discord.ui.UserSelect):
    def __init__(
        self,
        *,
        action: MemberAction,
        placeholder: str,
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        guild = interaction.guild
        member = value if isinstance(value, discord.Member) else None
        if member is None and guild is not None:
            member = guild.get_member(value.id)
        if member is None:
            await interaction.response.send_message(
                "❌ Membre introuvable.",
                ephemeral=True,
            )
            return
        await self.action(interaction, member)


class MemberPickerView(discord.ui.View):
    def __init__(
        self,
        *,
        action: MemberAction,
        placeholder: str,
    ) -> None:
        super().__init__(timeout=60)
        self.add_item(MemberPicker(action=action, placeholder=placeholder))


class TempVoiceControlView(discord.ui.View):
    """Persistent generic controls for the user's current managed temporary VC."""

    def __init__(self, cog: "TempVCControlsCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Verrouiller",
        emoji="🔒",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_vc_control_lock",
        row=0,
    )
    async def lock(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.set_locked(interaction, True)

    @discord.ui.button(
        label="Déverrouiller",
        emoji="🔓",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_vc_control_unlock",
        row=0,
    )
    async def unlock(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.set_locked(interaction, False)

    @discord.ui.button(
        label="Masquer",
        emoji="🙈",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_vc_control_hide",
        row=0,
    )
    async def hide(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.set_hidden(interaction, True)

    @discord.ui.button(
        label="Afficher",
        emoji="👁️",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_vc_control_show",
        row=0,
    )
    async def show(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.set_hidden(interaction, False)

    @discord.ui.button(
        label="Claim",
        emoji="👑",
        style=discord.ButtonStyle.primary,
        custom_id="temp_vc_control_claim",
        row=1,
    )
    async def claim(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.claim(interaction)

    @discord.ui.button(
        label="Limite",
        emoji="👥",
        style=discord.ButtonStyle.primary,
        custom_id="temp_vc_control_limit",
        row=1,
    )
    async def limit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        channel = await self.cog.require_control_channel(
            interaction, require_owner=True
        )
        if channel is None:
            return
        await interaction.response.send_modal(LimitModal(self.cog))

    async def _open_picker(
        self,
        interaction: discord.Interaction,
        *,
        action: MemberAction,
        placeholder: str,
        require_owner: bool = True,
    ) -> None:
        channel = await self.cog.require_control_channel(
            interaction, require_owner=require_owner
        )
        if channel is None:
            return
        await interaction.response.send_message(
            "Sélectionne un membre :",
            view=MemberPickerView(action=action, placeholder=placeholder),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Autoriser",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="temp_vc_control_trust",
        row=2,
    )
    async def trust(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open_picker(
            interaction,
            action=self.cog.trust_member,
            placeholder="Membre à autoriser",
        )

    @discord.ui.button(
        label="Retirer accès",
        emoji="⛔",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_vc_control_untrust",
        row=2,
    )
    async def untrust(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open_picker(
            interaction,
            action=self.cog.untrust_member,
            placeholder="Membre dont retirer l'accès",
        )

    @discord.ui.button(
        label="Bloquer",
        emoji="🚫",
        style=discord.ButtonStyle.danger,
        custom_id="temp_vc_control_block",
        row=3,
    )
    async def block(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open_picker(
            interaction,
            action=self.cog.block_member,
            placeholder="Membre à bloquer",
        )

    @discord.ui.button(
        label="Débloquer",
        emoji="✔️",
        style=discord.ButtonStyle.secondary,
        custom_id="temp_vc_control_unblock",
        row=3,
    )
    async def unblock(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open_picker(
            interaction,
            action=self.cog.unblock_member,
            placeholder="Membre à débloquer",
        )

    @discord.ui.button(
        label="Expulser",
        emoji="👢",
        style=discord.ButtonStyle.danger,
        custom_id="temp_vc_control_kick",
        row=3,
    )
    async def kick(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open_picker(
            interaction,
            action=self.cog.kick_member,
            placeholder="Membre à expulser du vocal",
        )

    @discord.ui.button(
        label="Transférer",
        emoji="🔁",
        style=discord.ButtonStyle.primary,
        custom_id="temp_vc_control_transfer",
        row=4,
    )
    async def transfer(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open_picker(
            interaction,
            action=self.cog.transfer_owner,
            placeholder="Nouveau propriétaire",
        )


class TempVCControlsCog(commands.Cog):
    """VoiceForge-inspired ownership and access controls layered over TempVCCog."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._owners: Dict[int, int] = load_temp_vc_owners()
        self._panel_posted: set[int] = set()

    async def _persist(self) -> None:
        await save_temp_vc_owners_async(self._owners.copy())

    async def _set_owner(self, channel_id: int, owner_id: int) -> None:
        self._owners[channel_id] = owner_id
        await self._persist()

    async def _forget_channel(self, channel_id: int) -> None:
        if self._owners.pop(channel_id, None) is not None:
            await self._persist()
        self._panel_posted.discard(channel_id)

    def owner_id(self, channel_id: int) -> int | None:
        return self._owners.get(channel_id)

    def _member_is_staff(self, member: discord.Member) -> bool:
        return bool(getattr(member.guild_permissions, "manage_channels", False))

    def _owner_is_present(
        self, channel: discord.VoiceChannel, owner_id: int | None
    ) -> bool:
        if owner_id is None:
            return False
        return any(member.id == owner_id for member in channel.members)

    async def require_control_channel(
        self,
        interaction: discord.Interaction,
        *,
        require_owner: bool,
    ) -> discord.VoiceChannel | None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "❌ Action disponible uniquement sur le serveur.",
                ephemeral=True,
            )
            return None

        voice = member.voice
        channel = voice.channel if voice else None
        if (
            not isinstance(channel, discord.VoiceChannel)
            or channel.id not in TEMP_VC_IDS
            or channel.id not in self._owners
        ):
            await interaction.response.send_message(
                "❌ Tu dois être dans un salon vocal temporaire contrôlé par le Refuge.",
                ephemeral=True,
            )
            return None

        if require_owner:
            owner_id = self._owners.get(channel.id)
            if owner_id != member.id and not self._member_is_staff(member):
                await interaction.response.send_message(
                    "❌ Seul le propriétaire du salon (ou un modérateur) peut faire ça.",
                    ephemeral=True,
                )
                return None

        return channel

    async def _maybe_post_panel(self, channel: discord.VoiceChannel) -> None:
        if channel.id in self._panel_posted:
            return
        sender = getattr(channel, "send", None)
        if not callable(sender):
            return
        try:
            await sender(embed=build_control_embed(), view=TempVoiceControlView(self))
        except (discord.Forbidden, discord.HTTPException):
            logger.info(
                "[temp_vc_controls] panneau non envoyé dans le vocal %s",
                channel.id,
            )
            return
        self._panel_posted.add(channel.id)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        changed = False
        for channel_id in list(self._owners):
            channel = self.bot.get_channel(channel_id)
            if (
                not isinstance(channel, discord.VoiceChannel)
                or channel_id not in TEMP_VC_IDS
            ):
                self._owners.pop(channel_id, None)
                self._panel_posted.discard(channel_id)
                changed = True
        if changed:
            await self._persist()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        channel = after.channel
        if not isinstance(channel, discord.VoiceChannel):
            return
        if channel.id not in TEMP_VC_IDS:
            return

        before_channel = before.channel
        came_from_standard_lobby = (
            before_channel is not None and before_channel.id == LOBBY_VC_ID
        )
        if channel.id not in self._owners and came_from_standard_lobby:
            await self._set_owner(channel.id, member.id)

        if self._owners.get(channel.id) == member.id:
            await self._maybe_post_panel(channel)

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self, channel: discord.abc.GuildChannel
    ) -> None:
        await self._forget_channel(channel.id)

    async def set_locked(
        self, interaction: discord.Interaction, locked: bool
    ) -> None:
        channel = await self.require_control_channel(
            interaction, require_owner=True
        )
        if channel is None or interaction.guild is None:
            return

        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = False if locked else None
        await channel.set_permissions(
            interaction.guild.default_role,
            overwrite=overwrite,
            reason=f"Temp VC {'lock' if locked else 'unlock'} par {interaction.user}",
        )
        await interaction.response.send_message(
            "🔒 Salon verrouillé." if locked else "🔓 Salon déverrouillé.",
            ephemeral=True,
        )

    async def set_hidden(
        self, interaction: discord.Interaction, hidden: bool
    ) -> None:
        channel = await self.require_control_channel(
            interaction, require_owner=True
        )
        if channel is None or interaction.guild is None:
            return

        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.view_channel = False if hidden else None
        await channel.set_permissions(
            interaction.guild.default_role,
            overwrite=overwrite,
            reason=f"Temp VC {'hide' if hidden else 'show'} par {interaction.user}",
        )
        await interaction.response.send_message(
            "🙈 Salon masqué." if hidden else "👁️ Salon visible.",
            ephemeral=True,
        )

    async def claim(self, interaction: discord.Interaction) -> None:
        channel = await self.require_control_channel(
            interaction, require_owner=False
        )
        if channel is None:
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            return

        owner_id = self._owners.get(channel.id)
        if owner_id == member.id:
            await interaction.response.send_message(
                "👑 Tu es déjà propriétaire de ce salon.",
                ephemeral=True,
            )
            return

        if self._owner_is_present(channel, owner_id) and not self._member_is_staff(member):
            await interaction.response.send_message(
                "❌ Le propriétaire actuel est encore dans le salon.",
                ephemeral=True,
            )
            return

        await self._set_owner(channel.id, member.id)
        await interaction.response.send_message(
            "👑 Tu es maintenant propriétaire du salon.",
            ephemeral=True,
        )

    async def set_user_limit(
        self, interaction: discord.Interaction, raw_limit: str
    ) -> None:
        channel = await self.require_control_channel(
            interaction, require_owner=True
        )
        if channel is None:
            return

        try:
            value = int(raw_limit.strip())
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "❌ Entre un nombre de 0 à 99.",
                ephemeral=True,
            )
            return

        if not 0 <= value <= 99:
            await interaction.response.send_message(
                "❌ La limite doit être comprise entre 0 et 99.",
                ephemeral=True,
            )
            return

        await channel.edit(
            user_limit=value,
            reason=f"Limite Temp VC modifiée par {interaction.user}",
        )
        text = "aucune limite" if value == 0 else f"{value} membre(s)"
        await interaction.response.send_message(
            f"👥 Limite mise à jour : **{text}**.",
            ephemeral=True,
        )

    async def _set_member_access(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        *,
        view_channel: bool | None,
        connect: bool | None,
        speak: bool | None,
        message: str,
        reason: str,
    ) -> None:
        channel = await self.require_control_channel(
            interaction, require_owner=True
        )
        if channel is None:
            return

        if target.bot:
            await interaction.response.send_message(
                "❌ Cette action n'est pas prévue pour les bots.",
                ephemeral=True,
            )
            return

        overwrite = channel.overwrites_for(target)
        overwrite.view_channel = view_channel
        overwrite.connect = connect
        overwrite.speak = speak
        await channel.set_permissions(
            target,
            overwrite=overwrite,
            reason=reason,
        )
        await interaction.response.send_message(message, ephemeral=True)

    async def trust_member(
        self, interaction: discord.Interaction, target: discord.Member
    ) -> None:
        await self._set_member_access(
            interaction,
            target,
            view_channel=True,
            connect=True,
            speak=True,
            message=f"✅ {target.mention} est autorisé dans le salon.",
            reason=f"Temp VC trust par {interaction.user}",
        )

    async def untrust_member(
        self, interaction: discord.Interaction, target: discord.Member
    ) -> None:
        await self._set_member_access(
            interaction,
            target,
            view_channel=None,
            connect=None,
            speak=None,
            message=f"⛔ Accès spécifique retiré pour {target.mention}.",
            reason=f"Temp VC untrust par {interaction.user}",
        )

    async def block_member(
        self, interaction: discord.Interaction, target: discord.Member
    ) -> None:
        channel = await self.require_control_channel(
            interaction, require_owner=True
        )
        if channel is None:
            return

        owner_id = self._owners.get(channel.id)
        if target.id == owner_id:
            await interaction.response.send_message(
                "❌ Le propriétaire ne peut pas se bloquer lui-même.",
                ephemeral=True,
            )
            return

        overwrite = channel.overwrites_for(target)
        overwrite.view_channel = False
        overwrite.connect = False
        overwrite.speak = False
        await channel.set_permissions(
            target,
            overwrite=overwrite,
            reason=f"Temp VC block par {interaction.user}",
        )
        if target.voice and target.voice.channel == channel:
            try:
                await target.move_to(None, reason="Bloqué du salon temporaire")
            except discord.HTTPException:
                logger.exception(
                    "[temp_vc_controls] impossible de déconnecter %s",
                    target.id,
                )
        await interaction.response.send_message(
            f"🚫 {target.mention} est bloqué.",
            ephemeral=True,
        )

    async def unblock_member(
        self, interaction: discord.Interaction, target: discord.Member
    ) -> None:
        await self._set_member_access(
            interaction,
            target,
            view_channel=None,
            connect=None,
            speak=None,
            message=f"✔️ {target.mention} est débloqué.",
            reason=f"Temp VC unblock par {interaction.user}",
        )

    async def kick_member(
        self, interaction: discord.Interaction, target: discord.Member
    ) -> None:
        channel = await self.require_control_channel(
            interaction, require_owner=True
        )
        if channel is None:
            return

        if not target.voice or target.voice.channel != channel:
            await interaction.response.send_message(
                "❌ Ce membre n'est pas dans ton salon vocal.",
                ephemeral=True,
            )
            return

        if target.id == self._owners.get(channel.id):
            await interaction.response.send_message(
                "❌ Le propriétaire ne peut pas s'expulser lui-même.",
                ephemeral=True,
            )
            return

        await target.move_to(
            None,
            reason=f"Expulsé du Temp VC par {interaction.user}",
        )
        await interaction.response.send_message(
            f"👢 {target.mention} a été expulsé du vocal.",
            ephemeral=True,
        )

    async def transfer_owner(
        self, interaction: discord.Interaction, target: discord.Member
    ) -> None:
        channel = await self.require_control_channel(
            interaction, require_owner=True
        )
        if channel is None:
            return

        if target not in channel.members:
            await interaction.response.send_message(
                "❌ Le nouveau propriétaire doit être présent dans le salon.",
                ephemeral=True,
            )
            return

        await self._set_owner(channel.id, target.id)
        await interaction.response.send_message(
            f"🔁 {target.mention} est maintenant propriétaire du salon.",
            ephemeral=True,
        )

    @app_commands.command(
        name="vocal_panel",
        description="Publier le panneau de contrôle des salons vocaux temporaires.",
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def post_control_panel(
        self, interaction: discord.Interaction
    ) -> None:
        channel = interaction.channel
        sender = getattr(channel, "send", None)
        if not callable(sender):
            await interaction.response.send_message(
                "❌ Impossible de publier le panneau dans ce salon.",
                ephemeral=True,
            )
            return

        await sender(
            embed=build_control_embed(),
            view=TempVoiceControlView(self),
        )
        await interaction.response.send_message(
            "✅ Panneau vocal publié.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    cog = TempVCControlsCog(bot)
    await bot.add_cog(cog)
    bot.add_view(TempVoiceControlView(cog))
