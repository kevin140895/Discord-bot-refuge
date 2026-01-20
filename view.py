import logging
import discord
from discord import app_commands

from config import (
    TRIGGER_CHANNEL_ID,
    ROLE_ANTHYX_COMMUNITY,
    ROLE_CONSOLE,
    ROLE_MOBILE,
    ROLE_NOTIFICATION,
    ROLE_PARIS_SPORTIFS,
    ROLE_PC,
)


class RoleView(discord.ui.View):
    """Vue de gestion des rôles pour le profil joueur."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    # ── Plateformes (exclusives) ─────────────────────────────────────────
    @discord.ui.button(
        label="PC 💻",
        style=discord.ButtonStyle.primary,
        custom_id="role_platform_pc",
        row=0,
    )
    async def btn_platform_pc(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._set_platform_role(interaction, ROLE_PC)

    @discord.ui.button(
        label="Consoles 🎮",
        style=discord.ButtonStyle.primary,
        custom_id="role_platform_console",
        row=0,
    )
    async def btn_platform_console(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._set_platform_role(interaction, ROLE_CONSOLE)

    @discord.ui.button(
        label="Mobile 📱",
        style=discord.ButtonStyle.primary,
        custom_id="role_platform_mobile",
        row=0,
    )
    async def btn_platform_mobile(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._set_platform_role(interaction, ROLE_MOBILE)

    # ── Intérêts (toggles) ───────────────────────────────────────────────
    @discord.ui.button(
        label="Notifications 🔔",
        style=discord.ButtonStyle.success,
        custom_id="role_interest_notifications",
        row=1,
    )
    async def btn_interest_notifications(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._toggle_role(interaction, ROLE_NOTIFICATION)

    @discord.ui.button(
        label="Anthyx Community 👾",
        style=discord.ButtonStyle.secondary,
        custom_id="role_interest_community",
        row=1,
    )
    async def btn_interest_community(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._toggle_role(interaction, ROLE_ANTHYX_COMMUNITY)

    @discord.ui.button(
        label="Paris Sportifs 🎯",
        style=discord.ButtonStyle.secondary,
        custom_id="role_interest_paris",
        row=1,
    )
    async def btn_interest_paris(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._toggle_role(interaction, ROLE_PARIS_SPORTIFS)

    # ── Reset ────────────────────────────────────────────────────────────
    @discord.ui.button(
        label="Tout effacer 🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="role_reset_all",
        row=2,
    )
    async def btn_reset_all(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._reset_roles(interaction)

    # ── Helpers ──────────────────────────────────────────────────────────
    async def _ensure_permissions(
        self, interaction: discord.Interaction
    ) -> bool:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                "❌ Action impossible en message privé.", ephemeral=True
            )
            return False
        me = guild.me or guild.get_member(interaction.client.user.id)  # type: ignore[union-attr]
        if not me:
            await interaction.response.send_message(
                "❌ Impossible de vérifier mes permissions.", ephemeral=True
            )
            return False
        if not me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "❌ Je n'ai pas la permission **Gérer les rôles**.",
                ephemeral=True,
            )
            return False
        return True

    async def _set_platform_role(
        self, interaction: discord.Interaction, role_id: int
    ) -> None:
        """Assigne une plateforme unique et retire les autres."""
        if not await self._ensure_permissions(interaction):
            return
        guild = interaction.guild
        if not guild:
            return

        member = interaction.user
        role = guild.get_role(role_id)
        if not role:
            await interaction.response.send_message(
                "❌ Rôle introuvable.", ephemeral=True
            )
            return

        other_platform_ids = {
            ROLE_PC,
            ROLE_CONSOLE,
            ROLE_MOBILE,
        } - {role_id}
        other_platform_roles = [
            guild.get_role(rid) for rid in other_platform_ids
        ]
        remove_list = [r for r in other_platform_roles if r and r in member.roles]

        try:
            if remove_list:
                await member.remove_roles(
                    *remove_list, reason="Changement de plateforme"
                )
            if role not in member.roles:
                await member.add_roles(
                    role, reason="Ajout plateforme principale"
                )
            await interaction.response.send_message(
                "🔄 Ta plateforme principale est maintenant mise à jour.",
                ephemeral=True,
            )
        except discord.Forbidden:
            logging.warning(
                "Permissions insuffisantes pour modifier la plateforme."
            )
            await interaction.response.send_message(
                "❌ Permissions insuffisantes pour modifier tes rôles.",
                ephemeral=True,
            )
        except discord.NotFound:
            logging.warning("Rôle ou membre introuvable.")
            await interaction.response.send_message(
                "❌ Rôle ou membre introuvable.", ephemeral=True
            )
        except discord.HTTPException as e:
            logging.error("Erreur HTTP lors du changement de plateforme: %s", e)
            await interaction.response.send_message(
                "❌ Erreur lors de la modification des rôles.",
                ephemeral=True,
            )
        except Exception as e:  # pragma: no cover - cas inattendu
            logging.exception("Erreur inattendue changement de plateforme: %s", e)
            await interaction.response.send_message(
                "❌ Impossible de modifier tes rôles.", ephemeral=True
            )

    async def _toggle_role(
        self, interaction: discord.Interaction, role_id: int
    ) -> None:
        """Ajoute ou retire un rôle d'intérêt."""
        if not await self._ensure_permissions(interaction):
            return
        guild = interaction.guild
        if not guild:
            return

        member = interaction.user
        role = guild.get_role(role_id)
        if not role:
            await interaction.response.send_message(
                "❌ Rôle introuvable.", ephemeral=True
            )
            return
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Retrait badge")
                await interaction.response.send_message(
                    "❌ Badge retiré", ephemeral=True
                )
            else:
                await member.add_roles(role, reason="Ajout badge")
                await interaction.response.send_message(
                    "✅ Badge ajouté", ephemeral=True
                )
        except discord.Forbidden:
            logging.warning("Permissions insuffisantes pour modifier un badge.")
            await interaction.response.send_message(
                "❌ Permissions insuffisantes pour modifier tes rôles.",
                ephemeral=True,
            )
        except discord.NotFound:
            logging.warning("Rôle ou membre introuvable pour un badge.")
            await interaction.response.send_message(
                "❌ Rôle ou membre introuvable.", ephemeral=True
            )
        except discord.HTTPException as e:
            logging.error("Erreur HTTP lors du toggle badge: %s", e)
            await interaction.response.send_message(
                "❌ Erreur lors de la modification des rôles.",
                ephemeral=True,
            )
        except Exception as e:  # pragma: no cover - cas inattendu
            logging.exception("Erreur inattendue toggle badge: %s", e)
            await interaction.response.send_message(
                "❌ Impossible de modifier tes rôles.", ephemeral=True
            )

    async def _reset_roles(self, interaction: discord.Interaction) -> None:
        """Retire tous les rôles de plateforme et d'intérêt."""
        if not await self._ensure_permissions(interaction):
            return
        guild = interaction.guild
        if not guild:
            return

        member = interaction.user
        role_ids = [
            ROLE_PC,
            ROLE_CONSOLE,
            ROLE_MOBILE,
            ROLE_NOTIFICATION,
            ROLE_ANTHYX_COMMUNITY,
            ROLE_PARIS_SPORTIFS,
        ]
        roles = [guild.get_role(role_id) for role_id in role_ids]
        remove_list = [role for role in roles if role and role in member.roles]

        try:
            if remove_list:
                await member.remove_roles(
                    *remove_list, reason="Reset rôles profil"
                )
            await interaction.response.send_message(
                "🧹 Ton profil a été nettoyé, tous les badges sont retirés.",
                ephemeral=True,
            )
        except discord.Forbidden:
            logging.warning("Permissions insuffisantes pour reset rôles.")
            await interaction.response.send_message(
                "❌ Permissions insuffisantes pour modifier tes rôles.",
                ephemeral=True,
            )
        except discord.NotFound:
            logging.warning("Rôle ou membre introuvable lors du reset.")
            await interaction.response.send_message(
                "❌ Rôle ou membre introuvable.", ephemeral=True
            )
        except discord.HTTPException as e:
            logging.error("Erreur HTTP lors du reset rôles: %s", e)
            await interaction.response.send_message(
                "❌ Erreur lors de la modification des rôles.",
                ephemeral=True,
            )
        except Exception as e:  # pragma: no cover - cas inattendu
            logging.exception("Erreur inattendue reset rôles: %s", e)
            await interaction.response.send_message(
                "❌ Impossible de modifier tes rôles.", ephemeral=True
            )


class PlayerTypeView(discord.ui.View):
    """Boutons de rôles :
        - Plateformes (PC/Consoles/Mobile) : exclusives entre elles
        - Notifications : toggle indépendant (coexiste avec n'importe quelle plateforme)
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)  # Vue persistante

    # ── Plateformes (exclusives) ─────────────────────────────────────────
    @discord.ui.button(label="💻 PC", style=discord.ButtonStyle.primary, custom_id="role_pc")
    async def btn_pc(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_platform_role(interaction, ROLE_PC, "PC")

    @discord.ui.button(
        label="🎮 Consoles",
        style=discord.ButtonStyle.primary,
        custom_id="role_console",
    )
    async def btn_console(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_platform_role(interaction, ROLE_CONSOLE, "Consoles")

    @discord.ui.button(
        label="📱 Mobile",
        style=discord.ButtonStyle.primary,
        custom_id="role_mobile",
    )
    async def btn_mobile(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_platform_role(interaction, ROLE_MOBILE, "Mobile")

    # ── Notifications (toggle indépendant) ───────────────────────────────
    @discord.ui.button(
        label="🔔 Notifications",
        style=discord.ButtonStyle.secondary,
        custom_id="role_notifications",
    )
    async def btn_notify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._toggle_role(interaction, ROLE_NOTIFICATION, "Notifications")

    @discord.ui.button(
        label="👾 Anthyx Community",
        style=discord.ButtonStyle.secondary,
        custom_id="role_anthyx_community",
    )
    async def btn_anthyx_community(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._toggle_role(interaction, ROLE_ANTHYX_COMMUNITY, "Anthyx Community")

    @discord.ui.button(
        label="🎯 Paris Sportifs",
        style=discord.ButtonStyle.secondary,
        custom_id="role_paris_sportifs",
    )
    async def btn_paris_sportifs(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._toggle_role(interaction, ROLE_PARIS_SPORTIFS, "Paris Sportifs")

    # ── Helpers ──────────────────────────────────────────────────────────
    async def _set_platform_role(
        self, interaction: discord.Interaction, role_id: int, label: str
    ) -> None:
        """Gère les rôles de plateformes (exclusifs)."""
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message(
                "❌ Action impossible en message privé.", ephemeral=True
            )

        role = guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message(
                f"❌ Rôle introuvable ({label}).", ephemeral=True
            )

        member = interaction.user
        try:
            # a) S'il a déjà cette plateforme -> NO-OP (aucun retrait)
            if role in member.roles:
                return await interaction.response.send_message(
                    f"✅ Tu es déjà sur **{label}** (aucun changement).",
                    ephemeral=True,
                )

            # b) Sinon -> ajouter cette plateforme et retirer les autres plateformes
            other_platform_ids = {ROLE_PC, ROLE_CONSOLE, ROLE_MOBILE} - {role_id}
            other_platform_roles = [
                guild.get_role(rid) for rid in other_platform_ids
            ]
            remove_list = [r for r in other_platform_roles if r and r in member.roles]

            if remove_list:
                await member.remove_roles(
                    *remove_list, reason=f"Changement de plateforme -> {label}"
                )

            await member.add_roles(
                role, reason=f"Ajout rôle plateforme {label}"
            )

            removed_txt = (
                f" (retiré: {', '.join(f'**{r.name}**' for r in remove_list)})"
                if remove_list
                else ""
            )
            await interaction.response.send_message(
                f"✅ Plateforme mise à jour : **{label}**{removed_txt}.\n"
                f"🔔 *Le rôle Notifications est conservé.*",
                ephemeral=True,
            )

        except discord.Forbidden:
            logging.warning(
                f"Permissions insuffisantes pour définir la plateforme {label}"
            )
            await interaction.response.send_message(
                "❌ Permissions insuffisantes pour modifier tes rôles.",
                ephemeral=True,
            )
        except discord.NotFound:
            logging.warning(
                f"Rôle ou membre introuvable lors de la définition de {label}"
            )
            await interaction.response.send_message(
                "❌ Rôle ou membre introuvable.", ephemeral=True
            )
        except discord.HTTPException as e:
            logging.error(
                f"Erreur HTTP lors de la définition de la plateforme {label}: {e}"
            )
            await interaction.response.send_message(
                "❌ Erreur lors de la modification des rôles.",
                ephemeral=True,
            )
        except Exception as e:  # pragma: no cover - cas inattendu
            logging.exception(f"Erreur inattendue set_platform {label}: {e}")
            await interaction.response.send_message(
                "❌ Impossible de modifier tes rôles.", ephemeral=True
            )

    async def _toggle_role(
        self, interaction: discord.Interaction, role_id: int, label: str
    ) -> None:
        """Ajoute ou retire le rôle donné (utilisé pour 🔔 Notifications)."""
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message(
                "❌ Action impossible en message privé.", ephemeral=True
            )

        role = guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message(
                f"❌ Rôle introuvable ({label}).", ephemeral=True
            )

        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason=f"Retrait rôle {label}")
                await interaction.response.send_message(
                    f"🔕 Rôle **{label}** retiré.", ephemeral=True
                )
            else:
                await member.add_roles(role, reason=f"Ajout rôle {label}")
                await interaction.response.send_message(
                    f"🔔 Rôle **{label}** ajouté.", ephemeral=True
                )
        except discord.Forbidden:
            logging.warning(
                f"Permissions insuffisantes pour modifier le rôle {label}"
            )
            await interaction.response.send_message(
                "❌ Permissions insuffisantes pour modifier tes rôles.",
                ephemeral=True,
            )
        except discord.NotFound:
            logging.warning(
                f"Rôle ou membre introuvable lors de la modification de {label}"
            )
            await interaction.response.send_message(
                "❌ Rôle ou membre introuvable.", ephemeral=True
            )
        except discord.HTTPException as e:
            logging.error(
                f"Erreur HTTP lors de la modification du rôle {label}: {e}"
            )
            await interaction.response.send_message(
                "❌ Erreur lors de la modification des rôles.",
                ephemeral=True,
            )
        except Exception as e:  # pragma: no cover - cas inattendu
            logging.exception(f"Erreur inattendue toggle rôle {label}: {e}")
            await interaction.response.send_message(
                "❌ Impossible de modifier tes rôles.", ephemeral=True
            )


class RadioView(discord.ui.View):
    """Boutons pour sélectionner la station radio."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _dispatch(self, interaction: discord.Interaction, cmd: str) -> None:
        cog = interaction.client.get_cog("RadioCog")
        if not cog:
            await interaction.response.send_message(
                "❌ Radio indisponible.", ephemeral=True
            )
            return
        command = getattr(cog, cmd, None)
        if command:
            if isinstance(command, app_commands.Command):
                await command.callback(cog, interaction)
            else:
                await command(interaction)

    @discord.ui.button(
        label="Rap FR", style=discord.ButtonStyle.primary, custom_id="radio_rap_fr"
    )
    async def btn_radio_rap_fr(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._dispatch(interaction, "radio_rap_fr")

    @discord.ui.button(
        label="Rap US", style=discord.ButtonStyle.primary, custom_id="radio_rap"
    )
    async def btn_radio_rap(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._dispatch(interaction, "radio_rap")

    @discord.ui.button(
        label="Rock", style=discord.ButtonStyle.primary, custom_id="radio_rock"
    )
    async def btn_radio_rock(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._dispatch(interaction, "radio_rock")

    @discord.ui.button(
        label="Radio Hip-Hop", style=discord.ButtonStyle.primary, custom_id="radio_hiphop"
    )
    async def btn_radio_hiphop(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._dispatch(interaction, "radio_hiphop")


class RSVPView(discord.ui.View):
    """Boutons RSVP pour les événements de jeu."""

    def __init__(self, event_id: str) -> None:
        super().__init__(timeout=None)
        self.event_id = event_id

        yes = discord.ui.Button(
            label="✅ Je viens",
            style=discord.ButtonStyle.success,
            custom_id=f"rsvp:{event_id}:yes",
        )
        maybe = discord.ui.Button(
            label="🤔 Peut-être",
            style=discord.ButtonStyle.secondary,
            custom_id=f"rsvp:{event_id}:maybe",
        )
        no = discord.ui.Button(
            label="❌ Je passe",
            style=discord.ButtonStyle.danger,
            custom_id=f"rsvp:{event_id}:no",
        )
        yes.callback = self._yes
        maybe.callback = self._maybe
        no.callback = self._no
        self.add_item(yes)
        self.add_item(maybe)
        self.add_item(no)

    async def _handle(self, interaction: discord.Interaction, status: str) -> None:
        from utils.game_events import EVENTS, save_event
        from cogs.xp import award_xp

        evt = EVENTS.get(self.event_id)
        if not evt:
            await interaction.response.send_message(
                "❌ Événement introuvable.", ephemeral=True
            )
            return
        uid = str(interaction.user.id)
        evt.rsvps[uid] = status
        bonus = False
        if status == "yes" and not evt.first_bonus:
            if sum(1 for s in evt.rsvps.values() if s == "yes") == 1:
                evt.first_bonus = True
                bonus = True
                try:
                    await award_xp(interaction.user.id, 50)
                    logging.info("[game] Bonus XP pour %s", interaction.user.id)
                except Exception:
                    logging.exception("[game] Échec attribution bonus XP")
        await save_event(evt)
        msg = {
            "yes": "Tu participes ✅",
            "maybe": "Tu es peut-être 🤔",
            "no": "Participation annulée ❌",
        }.get(status, "Statut mis à jour")
        if bonus:
            msg += " (+50 XP)"
        try:
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    async def _yes(self, interaction: discord.Interaction) -> None:
        await self._handle(interaction, "yes")

    async def _maybe(self, interaction: discord.Interaction) -> None:
        await self._handle(interaction, "maybe")

    async def _no(self, interaction: discord.Interaction) -> None:
        await self._handle(interaction, "no")


class StreamerTempVoiceView(discord.ui.View):
    """Bouton persistant pour créer un vocal streamer."""

    def __init__(self, bot: discord.Client) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Créer mon vocal",
        style=discord.ButtonStyle.success,
        custom_id="streamer_temp_vc:create",
    )
    async def create_vocal(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild = interaction.guild
        trigger_channel = guild.get_channel(TRIGGER_CHANNEL_ID) if guild else None
        if trigger_channel and isinstance(trigger_channel, discord.abc.Messageable):
            if interaction.channel_id != TRIGGER_CHANNEL_ID:
                await interaction.response.send_message(
                    f"Utilise ce bouton dans <#{TRIGGER_CHANNEL_ID}>.",
                    ephemeral=True,
                )
                return

        cog = self.bot.get_cog("StreamerTempVCCog")
        if cog is None:
            await interaction.response.send_message(
                "Le système de vocal temporaire n'est pas disponible.",
                ephemeral=True,
            )
            return

        await cog.handle_create_request(interaction)
