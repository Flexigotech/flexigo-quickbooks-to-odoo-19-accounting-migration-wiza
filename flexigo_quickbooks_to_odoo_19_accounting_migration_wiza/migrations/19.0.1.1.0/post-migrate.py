# -*- coding: utf-8 -*-
"""1.1.0 (BW plan §2.4): every connection that existed before goes on exactly as bought: 'direct' (OAuth from this
server, its tokens encrypted here). Only from the 1.0.x line: a database already on 1.1 of another series (18.0.1.1.x
going to 19) keeps the route of each connection. SQL only: nothing else changes and nothing is called."""


def migrate(cr, version):
    if not version or tuple(int(part) for part in version.split('.')[2:]) >= (1, 1, 0):
        return
    cr.execute("UPDATE qb_connection SET fgt_route = 'direct'")
