# -*- coding: utf-8 -*-
"""What a logged-in user can reach through /web/dataset/call_kw (auth="user": portal users too).

Template of the subscription recipe (receta.md §11, test 13): it goes as it is in tests/test_seguridad_rpc.py of every
connector (and in tests/__init__.py); the module is taken from this file's own import path.

Odoo only refuses remote calls to methods named with a leading underscore or marked @api.private
(get_public_method, the same check call_kw runs). Every method this module adds that RPC can reach is a button
(action_*: the ones that touch FlexigoTech Cloud or a secret check that the caller is an administrator). What it only
overrides (create, write, name_search, button_validate…) was already public: Odoo or the module that added it answers
for it."""
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import TransactionCase, tagged

MODULE = __name__.rsplit('.tests.', 1)[0]      # odoo.addons.<this module>
# 'model.method' that the web client calls through RPC on purpose; each one checks who calls and has its own test
ON_PURPOSE = set()


@tagged('post_install', '-at_install')
class TestSeguridadRpc(TransactionCase):
    maxDiff = None      # the red names every open method, not the first ten

    def test_no_helper_is_reachable_by_rpc(self):
        """Every method this module adds that call_kw accepts is a button (action_*)."""
        abiertos = []
        for name in self.env.registry:
            model = self.env[name]
            mro = type(model).mro()
            ours = [getattr(cls, '__module__', '').startswith(MODULE + '.') for cls in mro]
            for i, cls in enumerate(mro):
                if not ours[i]:
                    continue
                # what an ancestor from another module defines was already public: that module answers for it
                below = [b for b, mine in zip(mro[i + 1:], ours[i + 1:]) if not mine]
                for attr, value in vars(cls).items():
                    if not callable(value) or attr.startswith('action_') or any(attr in vars(b) for b in below):
                        continue
                    try:
                        get_public_method(model, attr)
                    except (AccessError, AttributeError):
                        continue
                    abiertos.append('%s.%s' % (name, attr))
        self.assertEqual(sorted(set(abiertos) - ON_PURPOSE), [],
                         'callable by any logged-in user through /web/dataset/call_kw')
