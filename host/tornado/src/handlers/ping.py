import tornado
import tornado.web
import tornado.gen

from jbox_util import esc_sessname
from handlers.handler_base import JBoxHandler
from jbox_container import JBoxContainer


class PingHandler(JBoxHandler):
    @tornado.web.asynchronous
    @tornado.gen.coroutine
    def get(self):
        sessname = str(self.get_cookie("sessname")).replace('"', '')
        if self.is_valid_req(self):
            JBoxContainer.record_ping("/" + esc_sessname(sessname))
            self.set_status(status_code=204)
            self.finish()
        else:
            self.log_info("Invalid ping request for " + sessname)
            self.send_error(status_code=403)

