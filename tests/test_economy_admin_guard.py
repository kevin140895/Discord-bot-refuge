from types import SimpleNamespace
import pkgutil

import pytest

import cogs
from cogs.zz_economy_admin_guard import (
    MAX_MANUAL_XP_GRANT,
    harden_economy_admin_commands,
    require_manage_guild,
)


def test_guard_loads_after_mutating_economy_commands():
    discovered = [module.name for module in pkgutil.iter_modules(cogs.__path__)]

    assert discovered.index("zz_economy_admin_guard") > discovered.index("xp")
    assert discovered.index("zz_economy_admin_guard") > discovered.index("machine_a_sous")


def test_harden_replaces_role_checks_and_caps_manual_xp():
    legacy_don_check = object()
    legacy_ticket_check = object()
    amount_param = SimpleNamespace(max_value=None)

    don_xp = SimpleNamespace(
        checks=[legacy_don_check],
        _params={"montant": amount_param},
    )
    ticket = SimpleNamespace(checks=[legacy_ticket_check])
    machine = SimpleNamespace(
        get_command=lambda name: ticket if name == "ticket" else None,
    )
    commands = {"don_xp": don_xp, "machine": machine}
    bot = SimpleNamespace(
        tree=SimpleNamespace(get_command=lambda name: commands.get(name)),
    )

    harden_economy_admin_commands(bot)

    assert don_xp.checks == [require_manage_guild]
    assert ticket.checks == [require_manage_guild]
    assert amount_param.max_value == MAX_MANUAL_XP_GRANT == 10_000


def test_harden_fails_closed_when_expected_command_is_missing():
    bot = SimpleNamespace(
        tree=SimpleNamespace(get_command=lambda name: None),
    )

    with pytest.raises(RuntimeError, match="don_xp"):
        harden_economy_admin_commands(bot)
