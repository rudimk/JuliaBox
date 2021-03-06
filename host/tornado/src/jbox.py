#! /usr/bin/env python

import os.path
import random
import string

import tornado.ioloop
import tornado.web
import tornado.auth
import docker

from jbox_util import read_config, make_sure_path_exists, CloudHelper, LoggerMixin
from db.db_base import JBoxDB
from db.user_v2 import JBoxUserV2
from db.invites import JBoxInvite
from db.accounting_v2 import JBoxAccountingV2
from jbox_container import JBoxContainer
from handlers.handler_base import JBoxHandler
from handlers.admin import AdminHandler
from handlers.main import MainHandler
from handlers.auth import AuthHandler
from handlers.ping import PingHandler
from handlers.cors import CorsHandler


class JBox(LoggerMixin):
    cfg = None

    def __init__(self):
        cfg = JBox.cfg = read_config()
        dckr = docker.Client()
        cloud_cfg = cfg['cloud_host']

        JBoxHandler.configure(cfg)

        JBoxDB.configure(cfg)
        if 'jbox_users_v2' in cloud_cfg:
            JBoxUserV2.NAME = cloud_cfg['jbox_users_v2']
        if 'jbox_invites' in cloud_cfg:
            JBoxInvite.NAME = cloud_cfg['jbox_invites']
        if 'jbox_accounting_v2' in cloud_cfg:
            JBoxAccountingV2.NAME = cloud_cfg['jbox_accounting_v2']

        CloudHelper.configure(has_s3=cloud_cfg['s3'], has_dynamodb=cloud_cfg['dynamodb'],
                              has_cloudwatch=cloud_cfg['cloudwatch'], region=cloud_cfg['region'],
                              install_id=cloud_cfg['install_id'])

        backup_location = os.path.expanduser(cfg['backup_location'])
        user_home_img = os.path.expanduser(cfg['user_home_image'])
        mnt_location = os.path.expanduser(cfg['mnt_location'])
        backup_bucket = cloud_cfg['backup_bucket']
        make_sure_path_exists(backup_location)
        JBoxContainer.configure(dckr, cfg['docker_image'],
                                cfg['mem_limit'], cfg['cpu_limit'], cfg['disk_limit'],
                                [os.path.join(mnt_location, '${DISK_ID}')],
                                mnt_location, backup_location, user_home_img,
                                cfg['numlocalmax'], cfg["numdisksmax"],
                                backup_bucket=backup_bucket)

        self.application = tornado.web.Application([
            (r"/", MainHandler),
            (r"/hostlaunchipnb/", AuthHandler),
            (r"/hostadmin/", AdminHandler),
            (r"/ping/", PingHandler),
            (r"/cors/", CorsHandler)
        ])
        cookie_secret = ''.join(random.choice(string.ascii_uppercase + string.digits) for x in xrange(32))
        self.application.settings["cookie_secret"] = cookie_secret
        self.application.settings["google_oauth"] = cfg["google_oauth"]
        self.application.listen(cfg["port"])

        self.ioloop = tornado.ioloop.IOLoop.instance()

        # run container maintainence every 5 minutes
        run_interval = 5 * 60 * 1000
        self.log_info("Container maintenance every " + str(run_interval / (60 * 1000)) + " minutes")
        self.ct = tornado.ioloop.PeriodicCallback(JBox.do_housekeeping, run_interval, self.ioloop)

    def run(self):
        JBoxContainer.publish_container_stats()
        self.ct.start()
        self.ioloop.start()

    @staticmethod
    def do_housekeeping():
        server_delete_timeout = JBox.cfg['expire']
        JBoxContainer.maintain(max_timeout=server_delete_timeout, inactive_timeout=JBox.cfg['inactivity_timeout'],
                               protected_names=JBox.cfg['protected_docknames'])
        if JBox.cfg['scale_down'] and (JBoxContainer.num_active() == 0) and \
                (JBoxContainer.num_stopped() == 0) and CloudHelper.should_terminate():
            JBox.log_info("terminating to scale down")
            CloudHelper.terminate_instance()


if __name__ == "__main__":
    JBox().run()
