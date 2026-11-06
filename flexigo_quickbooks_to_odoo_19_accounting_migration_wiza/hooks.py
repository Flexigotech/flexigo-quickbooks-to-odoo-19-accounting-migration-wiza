# -*- coding: utf-8 -*-
import logging
from functools import partial

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

NOT_CONNECTED = ('not_connected', 'permit_missing')


def _forget_cloud_credentials(env):
    """Delete from FlexigoTech Cloud the Intuit apps and tokens this database saved there: each family without
    @<connection> (quickbooks, quickbooks-test), which removes it for every connection. Always tried, whatever the
    connections say now: one deleted while the relay did not answer left its app there and no longer remembers it. If
    the relay does not answer, the module is uninstalled anyway and the log says so: "Disconnect" in Settings >
    FlexigoTech Cloud deletes them. Stops at the first failure: with the relay down every attempt can take 85 s."""
    from .models.qb_connection import FGT_MODULE, FGT_PLATFORMS
    cloud = env['flexigotech.cloud']
    for platform in FGT_PLATFORMS:
        try:
            cloud._relay_delete_credential(FGT_MODULE, platform)
        except Exception as err:  # noqa: BLE001 - never block an uninstall
            code = getattr(err, 'code', None)
            if code == 'unknown_platform':
                continue   # a relay without that entry keeps nothing there
            if code in NOT_CONNECTED:
                return
            _logger.warning("QuickBooks migration uninstall: could not delete the Intuit apps from FlexigoTech Cloud "
                            "(%s)", code or type(err).__name__)
            return


def _forget_after_uninstall(registry):
    """After the commit of the uninstall: only if the module did get uninstalled (an uninstall undone keeps every
    app). Never raises."""
    from .models.qb_connection import FGT_MODULE
    try:
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            if env['ir.module.module'].search_count([('name', '=', FGT_MODULE), ('state', '=', 'uninstalled')]):
                _forget_cloud_credentials(env)
    except Exception as err:  # noqa: BLE001 - after the commit nothing raises
        _logger.warning("QuickBooks migration uninstall: could not delete the Intuit apps from FlexigoTech Cloud "
                        "(%s)", type(err).__name__)


def uninstall_hook(env):
    """Delete the Intuit apps and tokens this database kept in FlexigoTech Cloud, after the commit of the uninstall
    (kb_conector_pasarela_pausas §9): the relay is not part of the transaction. Uninstalled together with FlexigoTech
    Cloud ('to remove' too), right away: its hook runs after this one and forgets the database's token before that
    commit. Nothing migrated into Odoo is touched."""
    if env['ir.module.module'].sudo().search_count([('name', '=', 'flexigotech_cloud'), ('state', '=', 'to remove')]):
        _forget_cloud_credentials(env)
    else:
        env.cr.postcommit.add(partial(_forget_after_uninstall, env.registry))
