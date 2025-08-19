import logging
import discord

from config import ROLE_PC, ROLE_CONSOLE, ROLE_MOBILE, ROLE_NOTIFICATION


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

