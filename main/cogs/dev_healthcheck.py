import json
import inspect
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks


class DevHealthcheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="healthcheck_all",
        description="Diagnostic global du bot (non destructif)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def healthcheck_all(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        report: dict[str, dict[str, Any]] = {
            "cogs": {},
            "commands": {},
            "tasks": {},
            "views": {},
            "config_files": {},
            "selfchecks": {},
        }

        # Cogs inventory
        pass_cogs = 0
        for name, cog in self.bot.cogs.items():
            try:
                report["cogs"][name] = {
                    "status": "PASS",
                    "detail": type(cog).__name__,
                }
                pass_cogs += 1
            except Exception as e:  # pragma: no cover - defensive
                report["cogs"][name] = {"status": "FAIL", "error": str(e)}

        # Commands inventory
        try:
            cmds = interaction.client.tree.get_commands()
            report["commands"] = {
                "status": "PASS" if cmds else "INFO",
                "count": len(cmds),
                "details": [
                    {
                        "name": c.name,
                        "cog": getattr(getattr(c.callback, "__self__", None), "__class__", type(None)).__name__,
                    }
                    for c in cmds
                ],
            }
        except Exception as e:  # pragma: no cover - defensive
            report["commands"] = {"status": "FAIL", "error": str(e), "count": 0, "details": []}

        # Tasks inventory
        total_tasks = 0
        pass_tasks = 0
        for cog_name, cog in self.bot.cogs.items():
            for attr_name in dir(cog):
                attr = getattr(cog, attr_name)
                if isinstance(attr, tasks.Loop):
                    total_tasks += 1
                    seconds = getattr(attr, "seconds", 0) or 0
                    minutes = getattr(attr, "minutes", 0) or 0
                    hours = getattr(attr, "hours", 0) or 0
                    interval = seconds + minutes * 60 + hours * 3600
                    try:
                        running = attr.is_running()
                    except Exception:  # pragma: no cover - defensive
                        running = False
                    status = "PASS" if interval > 0 else "FAIL"
                    if status == "PASS":
                        pass_tasks += 1
                    report["tasks"][f"{cog_name}.{attr_name}"] = {
                        "interval": interval,
                        "is_running": running,
                        "status": status,
                    }
        report["tasks_meta"] = {"total": total_tasks, "pass": pass_tasks}

        # Views inventory
        try:
            views = getattr(self.bot, "persistent_views", [])
            report["views"] = {
                "status": "PASS" if views else "INFO",
                "count": len(views),
            }
        except Exception as e:  # pragma: no cover - defensive
            report["views"] = {"status": "FAIL", "error": str(e), "count": 0}

        # Config files
        cfg_bases = [Path("data"), Path("main/data")]
        for base in cfg_bases:
            if base.exists():
                for path in base.rglob("*.json"):
                    try:
                        with open(path, "r", encoding="utf-8") as fp:
                            json.load(fp)
                        report["config_files"][str(path)] = {"status": "PASS"}
                    except Exception as e:  # pragma: no cover - defensive
                        report["config_files"][str(path)] = {
                            "status": "FAIL",
                            "error": str(e),
                        }

        # Self checks
        for name, cog in self.bot.cogs.items():
            func = getattr(cog, "_self_check_report", None)
            if func:
                try:
                    res = func()
                    if inspect.isawaitable(res):
                        res = await res
                    report["selfchecks"][name] = res
                except Exception as e:  # pragma: no cover - defensive
                    report["selfchecks"][name] = {"error": str(e)}

        from config import LEVEL_FEED_CHANNEL_ID, ENABLE_GAME_LEVEL_FEED
        from utils.messages import LEVEL_FEED_TEMPLATES

        lf_checks: dict[str, str] = {}
        ch = self.bot.get_channel(LEVEL_FEED_CHANNEL_ID)
        if isinstance(ch, discord.TextChannel):
            me = ch.guild.me
            perms = ch.permissions_for(me) if me else None
            lf_checks["channel"] = "PASS" if perms and perms.send_messages else "FAIL"
        else:
            lf_checks["channel"] = "FAIL"
        expected = {"pari_xp_up", "pari_xp_down", "machine_a_sous_up"}
        lf_checks["templates"] = (
            "PASS" if expected <= set(LEVEL_FEED_TEMPLATES) else "FAIL"
        )
        lf_checks["enabled"] = "PASS" if ENABLE_GAME_LEVEL_FEED else "WARN"
        report["selfchecks"]["level_feed"] = lf_checks

        # Markdown report
        md_path = Path("main/tests/health_report.md")
        try:
            lines: list[str] = ["# Healthcheck Report", ""]
            lines.append("## Cogs")
            lines.append("| Cog | Status | Detail |")
            lines.append("| --- | --- | --- |")
            for name, data in report["cogs"].items():
                lines.append(f"| {name} | {data['status']} | {data.get('detail', data.get('error', ''))} |")
            lines.append("")

            lines.append("## Commands")
            lines.append("| Name | Cog |")
            lines.append("| --- | --- |")
            for cmd in report["commands"].get("details", []):
                lines.append(f"| {cmd['name']} | {cmd['cog']} |")
            lines.append("")

            lines.append("## Tasks")
            lines.append("| Name | Interval | Running | Status |")
            lines.append("| --- | --- | --- | --- |")
            for name, data in report["tasks"].items():
                lines.append(
                    f"| {name} | {data['interval']} | {data['is_running']} | {data['status']} |"
                )
            lines.append("")

            lines.append("## Config files")
            lines.append("| File | Status |")
            lines.append("| --- | --- |")
            for path, data in report["config_files"].items():
                lines.append(f"| {path} | {data['status']} |")
            lines.append("")

            if report["selfchecks"]:
                lines.append("## Self-checks")
                lines.append("| Cog | Test | Status |")
                lines.append("| --- | --- | --- |")
                for cog_name, res in report["selfchecks"].items():
                    for key, val in res.items():
                        lines.append(f"| {cog_name} | {key} | {val} |")
                lines.append("")

            md_path.parent.mkdir(parents=True, exist_ok=True)
            with open(md_path, "w", encoding="utf-8") as fp:
                fp.write("\n".join(lines))
            report["markdown"] = str(md_path)
        except Exception as e:  # pragma: no cover - defensive
            report["markdown_error"] = str(e)

        # Embed summary
        color = discord.Color.green()
        if any(d.get("status") == "FAIL" for d in report["cogs"].values()):
            color = discord.Color.orange()
        if any(d.get("status") == "FAIL" for d in report["tasks"].values()):
            color = discord.Color.orange()
        if any(d.get("status") == "FAIL" for d in report["config_files"].values()):
            color = discord.Color.orange()
        if report["commands"].get("status") == "FAIL":
            color = discord.Color.orange()
        if report["views"].get("status") == "FAIL":
            color = discord.Color.orange()

        total_cogs = len(report["cogs"])
        total_tasks = report["tasks_meta"].get("total", 0)
        fail_tasks = [
            name
            for name, data in report["tasks"].items()
            if data.get("status") == "FAIL"
        ]
        fail_cfg = [
            path
            for path, data in report["config_files"].items()
            if data.get("status") == "FAIL"
        ]

        embed = discord.Embed(
            title="🩺 Healthcheck",
            description="Diagnostic global du bot",
            color=color,
        )
        embed.add_field(
            name="Cogs",
            value=f"{pass_cogs} PASS / {total_cogs}",
            inline=False,
        )
        embed.add_field(
            name="Commands",
            value=str(report["commands"].get("count", 0)),
            inline=False,
        )
        tasks_val = f"{pass_tasks} PASS / {total_tasks}"
        if fail_tasks:
            tasks_val += "\n" + "\n".join(f"❌ {t}" for t in fail_tasks)
        embed.add_field(name="Tasks", value=tasks_val, inline=False)
        cfg_val = "PASS" if not fail_cfg else "FAIL\n" + "\n".join(f"❌ {c}" for c in fail_cfg)
        embed.add_field(name="Config", value=cfg_val, inline=False)
        if report["selfchecks"]:
            for cog_name, res in report["selfchecks"].items():
                summary = "\n".join(f"{k}: {v}" for k, v in res.items())
                embed.add_field(
                    name=f"Self-check {cog_name}", value=summary or "-", inline=False
                )
        embed.set_footer(text="Ce diagnostic n'altère rien (add-only).")

        content = None
        if report.get("markdown"):
            content = report["markdown"]
        await interaction.followup.send(embed=embed, content=content, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DevHealthcheckCog(bot))
