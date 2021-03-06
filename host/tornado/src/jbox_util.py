import os
import sys
import time
import errno
import datetime
import pytz

import psutil
import isodate
import boto.dynamodb
import boto.utils
import boto.ec2
import boto.ec2.cloudwatch
from boto.s3.key import Key


def log_info(s):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print (ts + "  " + s)
    sys.stdout.flush()


# TODO: This scheme of escaping ssession names can result in clashes.
#       Change to a better scheme probably one using lower case and upper case characters
def esc_sessname(s):
    if None == s:
        return s
    return s.replace("@", "_at_").replace(".", "_")


def read_config():
    with open("conf/tornado.conf") as f:
        cfg = eval(f.read())

    def update_config(base_cfg, add_cfg):
        for n, v in add_cfg.iteritems():
            if (n in base_cfg) and isinstance(base_cfg[n], dict):
                update_config(base_cfg[n], v)
            else:
                base_cfg[n] = v

    if os.path.isfile("conf/jbox.user"):
        with open("conf/jbox.user") as f:
            ucfg = eval(f.read())
        update_config(cfg, ucfg)

    cfg["admin_sessnames"] = []
    for ad in cfg["admin_users"]:
        cfg["admin_sessnames"].append(esc_sessname(ad))

    cfg["protected_docknames"] = []
    for ps in cfg["protected_sessions"]:
        cfg["protected_docknames"].append("/" + esc_sessname(ps))

    return cfg


def make_sure_path_exists(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise


def _apply_to_path_element(path, file_fn, dir_fn, link_fn):
    if os.path.islink(path):
        link_fn(path)
    elif os.path.isfile(path):
        file_fn(path)
    elif os.path.isdir(path):
        dir_fn(path)
    else:
        raise Exception("Unknown file type for " + path)


def apply_to_path_elements(path, file_fn, dir_fn, link_fn, include_itself, topdown):
    for root, dirs, files in os.walk(path, topdown=topdown):
        for f in files:
            _apply_to_path_element(os.path.join(root, f), file_fn, dir_fn, link_fn)
        for d in dirs:
            _apply_to_path_element(os.path.join(root, d), file_fn, dir_fn, link_fn)

    if include_itself:
        _apply_to_path_element(path, file_fn, dir_fn, link_fn)


def ensure_writable(path, include_iteslf=False):
    apply_to_path_elements(path, lambda p: os.chmod(p, 0555), lambda p: os.chmod(p, 0777), lambda p: None,
                           include_iteslf, True)


def ensure_delete(path, include_itself=False):
    ensure_writable(path, include_itself)
    apply_to_path_elements(path, lambda p: os.remove(p), lambda p: os.rmdir(p), lambda p: os.remove(p), include_itself,
                           False)


def unquote(s):
    if None == s:
        return s
    s = s.strip()
    if s[0] == '"':
        return s[1:-1]
    else:
        return s


class LoggerMixin(object):
    @classmethod
    def log(cls, lvl, msg):
        log_info(lvl + ": " + cls.__name__ + ": " + msg)

    @classmethod
    def log_info(cls, msg):
        cls.log('INFO', msg)

    @classmethod
    def log_error(cls, msg):
        cls.log('INFO', msg)

    @classmethod
    def log_debug(cls, msg):
        cls.log('DEBUG', msg)


class CloudHelper(LoggerMixin):
    REGION = 'us-east-1'
    INSTALL_ID = 'JuliaBox'
    EC2_CONN = None
    DYNAMODB_CONN = None
    S3_CONN = None
    S3_BUCKETS = {}
    CLOUDWATCH_CONN = None
    ENABLED = {}
    INSTANCE_ID = None
    PUBLIC_HOSTNAME = None
    SELF_STATS = {}
    # STATS_CACHE = {} # TODO: cache stats

    @staticmethod
    def instance_id():
        if None == CloudHelper.INSTANCE_ID:
            if not CloudHelper.ENABLED['cloudwatch']:
                CloudHelper.INSTANCE_ID = 'localhost'
            else:
                CloudHelper.INSTANCE_ID = boto.utils.get_instance_metadata()['instance-id']
        return CloudHelper.INSTANCE_ID

    @staticmethod
    def instance_public_hostname():
        if None == CloudHelper.PUBLIC_HOSTNAME:
            if not CloudHelper.ENABLED['cloudwatch']:
                CloudHelper.PUBLIC_HOSTNAME = 'localhost'
            else:
                CloudHelper.PUBLIC_HOSTNAME = boto.utils.get_instance_metadata()['public-hostname']
        return CloudHelper.PUBLIC_HOSTNAME

    @staticmethod
    def instance_attrs(instance_id=None):
        if None == instance_id:
            instance_id = CloudHelper.instance_id()
        if CloudHelper.ENABLED['cloudwatch']:
            attrs = CloudHelper.connect_ec2().get_only_instances([instance_id])
            if len(attrs) > 0:
                return attrs[0]
        return None

    @staticmethod
    def uptime_minutes(instance_id=None):
        if CloudHelper.ENABLED['cloudwatch']:
            attrs = CloudHelper.instance_attrs(instance_id)
            lt = isodate.parse_datetime(attrs.launch_time)
            nt = datetime.datetime.now(pytz.utc)
            uptime = nt - lt
        elif instance_id is not None:
            uptime = datetime.datetime.now() - datetime.datetime.fromtimestamp(psutil.boot_time())
        else:
            CloudHelper.log_debug("cloudwatch disabled. can not get uptime")
            return 0
        minutes = (uptime.seconds + uptime.microseconds / 1000000.0) / 60.0
        return minutes

    @staticmethod
    def configure(has_s3=True, has_dynamodb=True, has_cloudwatch=True, region='us-east-1', install_id='JuliaBox'):
        CloudHelper.ENABLED['s3'] = has_s3
        CloudHelper.ENABLED['dynamodb'] = has_dynamodb
        CloudHelper.ENABLED['cloudwatch'] = has_cloudwatch
        CloudHelper.INSTALL_ID = install_id
        CloudHelper.REGION = region

    @staticmethod
    def connect_ec2():
        if (None == CloudHelper.EC2_CONN) and CloudHelper.ENABLED['cloudwatch']:
            CloudHelper.EC2_CONN = boto.ec2.connect_to_region(CloudHelper.REGION)
        return CloudHelper.EC2_CONN

    @staticmethod
    def connect_dynamodb():
        """ Return a connection to AWS DynamoDB at the configured region """
        if (None == CloudHelper.DYNAMODB_CONN) and CloudHelper.ENABLED['dynamodb']:
            CloudHelper.DYNAMODB_CONN = boto.dynamodb.connect_to_region(CloudHelper.REGION)
        return CloudHelper.DYNAMODB_CONN

    @staticmethod
    def connect_s3():
        if (None == CloudHelper.S3_CONN) and CloudHelper.ENABLED['s3']:
            CloudHelper.S3_CONN = boto.connect_s3()
        return CloudHelper.S3_CONN

    @staticmethod
    def connect_s3_bucket(bucket):
        if not CloudHelper.ENABLED['s3']:
            return None

        if bucket not in CloudHelper.S3_BUCKETS:
            CloudHelper.S3_BUCKETS[bucket] = CloudHelper.connect_s3().get_bucket(bucket)
        return CloudHelper.S3_BUCKETS[bucket]

    @staticmethod
    def connect_cloudwatch():
        if (None == CloudHelper.CLOUDWATCH_CONN) and CloudHelper.ENABLED['cloudwatch']:
            CloudHelper.CLOUDWATCH_CONN = boto.ec2.cloudwatch.connect_to_region(CloudHelper.REGION)
        return CloudHelper.CLOUDWATCH_CONN

    @staticmethod
    def get_metric_dimensions(metric_name, metric_namespace=None):
        if metric_namespace is None:
            metric_namespace = CloudHelper.INSTALL_ID

        metrics = CloudHelper.connect_cloudwatch().list_metrics()
        dims = {}
        for m in metrics:
            if m.name == metric_name and m.namespace == metric_namespace:
                for n_dim, v_dim in m.dimensions.iteritems():
                    dims[n_dim] = dims.get(n_dim, []) + v_dim
        if len(dims) == 0:
            CloudHelper.log_info("invalid metric " + '.'.join([metric_namespace, metric_name]))
            return None
        return dims

    @staticmethod
    def publish_stats(stat_name, stat_unit, stat_value):
        """ Publish custom cloudwatch statistics. Used for status monitoring and auto scaling. """
        CloudHelper.SELF_STATS[stat_name] = stat_value
        if not CloudHelper.ENABLED['cloudwatch']:
            return

        dims = {'InstanceID': CloudHelper.instance_id()}
        CloudHelper.log_info("CloudWatch " + CloudHelper.INSTALL_ID + "." + CloudHelper.instance_id() + "." + stat_name
                             + "=" + str(stat_value) + "(" + stat_unit + ")")
        CloudHelper.connect_cloudwatch().put_metric_data(namespace=CloudHelper.INSTALL_ID, name=stat_name,
                                                         unit=stat_unit, value=stat_value, dimensions=dims)

    @staticmethod
    def instance_accept_session_priority(instance_id, load):
        # uptime = CloudHelper.uptime_minutes(instance_id)
        # uptime_last_hour = uptime_mins % 60
        # TODO:
        # - ami changeover
        # - hourly window
        # - load
        return str(int(load)) + '_' + instance_id

    @staticmethod
    def terminate_instance(instance=None):
        if not CloudHelper.ENABLED['cloudwatch']:
            return

        if None == instance:
            instance = CloudHelper.instance_id()

        CloudHelper.log_info("Terminating instance: " + instance)
        CloudHelper.connect_ec2().terminate_instances(instance_ids=[instance])

    @staticmethod
    def should_terminate():
        if not CloudHelper.ENABLED['cloudwatch']:
            return False

        uptime = CloudHelper.uptime_minutes()

        # if uptime less than hour and half return false
        if uptime < 90:
            CloudHelper.log_debug("not terminating as uptime (" + repr(uptime) + ") < 90")
            return False

        if not CloudHelper.ENABLED['cloudwatch']:
            return False

        cluster_load = CloudHelper.get_cluster_stats('Load')

        # keep at least 1 machine running
        if len(cluster_load) == 1:
            CloudHelper.log_debug("not terminating as this is the only machine")
            return False

        # sort by load and instance_id
        sorted_nodes = sorted(cluster_load.iteritems(),
                              key=lambda x: CloudHelper.instance_accept_session_priority(x[0], x[1]))
        # if this is not the node with least load, keep running
        if sorted_nodes[-1][0] != CloudHelper.instance_id():
            CloudHelper.log_debug("not terminating as this is not the last node in sorted list")
            return False

        return True

    @staticmethod
    def should_accept_session():
        self_load = CloudHelper.get_instance_stats(CloudHelper.instance_id(), 'Load')
        CloudHelper.log_debug("Load self: " + repr(self_load))
        if self_load >= 100:
            return False

        if not CloudHelper.ENABLED['cloudwatch']:
            return True

        cluster_load = CloudHelper.get_cluster_stats('Load')
        avg_load = CloudHelper.get_cluster_average_stats('Load', results=cluster_load)
        CloudHelper.log_debug("Load cluster: " + repr(cluster_load) + " avg: " + repr(avg_load))

        # if not least loaded, accept
        if self_load >= avg_load:
            CloudHelper.log_debug("Accepting because this is not the least loaded (self load >= avg)")
            return True

        # exclude machines with load >= avg load
        filtered_nodes = {k: v for k, v in cluster_load.iteritems() if v >= avg_load}
        # if this is the only instance with load less than average, accept
        if len(filtered_nodes) == 1:
            CloudHelper.log_debug("Accepting because this is the only instance with load less than average")
            return True
        # sort by load and instance_id
        sorted_nodes = sorted(filtered_nodes.iteritems(),
                              key=lambda x: CloudHelper.instance_accept_session_priority(x[0], x[1]))
        # if this is not the node with least load, accept
        if sorted_nodes[0][1] != CloudHelper.instance_id():
            CloudHelper.log_debug("Accepting because this is not the node with least load")
            return True
        return False

    @staticmethod
    def get_instance_stats(instance, stat_name, namespace=None):
        if (instance == CloudHelper.instance_id()) and (stat_name in CloudHelper.SELF_STATS):
            CloudHelper.log_debug("Using cached self_stats. " + stat_name + "=" +
                                  repr(CloudHelper.SELF_STATS[stat_name]))
            return CloudHelper.SELF_STATS[stat_name]
        elif not CloudHelper.ENABLED['cloudwatch']:
            return None

        if namespace is None:
            namespace = CloudHelper.INSTALL_ID
        end = datetime.datetime.utcnow()
        start = end - datetime.timedelta(minutes=30)
        res = None
        results = CloudHelper.connect_cloudwatch().get_metric_statistics(60, start, end, stat_name, namespace,
                                                                         'Average', {'InstanceID': [instance]})
        for _res in results:
            if (res is None) or (res['Timestamp'] < _res['Timestamp']):
                res = _res
        return res['Average'] if res else None

    @staticmethod
    def get_cluster_average_stats(stat_name, namespace=None, results=None):
        if results is None:
            results = CloudHelper.get_cluster_stats(stat_name, namespace)

        vals = results.values()
        if len(vals) > 0:
            return float(sum(vals)) / len(vals)
        return None

    @staticmethod
    def get_cluster_stats(stat_name, namespace=None):
        if not CloudHelper.ENABLED['cloudwatch']:
            if stat_name in CloudHelper.SELF_STATS:
                return {CloudHelper.instance_id(): CloudHelper.SELF_STATS[stat_name]}
            else:
                return None

        dims = CloudHelper.get_metric_dimensions(stat_name, namespace)
        if None == dims:
            return None

        stats = {}
        if 'InstanceID' in dims:
            for instance in dims['InstanceID']:
                instance_load = CloudHelper.get_instance_stats(instance, stat_name, namespace)
                if instance_load is not None:
                    stats[instance] = instance_load

        return stats

    @staticmethod
    def push_file_to_s3(bucket, local_file, metadata=None):
        if not CloudHelper.ENABLED['s3']:
            return None

        key_name = os.path.basename(local_file)
        k = Key(CloudHelper.connect_s3_bucket(bucket))
        k.key = key_name
        if metadata is not None:
            for meta_name, meta_value in metadata.iteritems():
                k.set_metadata(meta_name, meta_value)
        k.set_contents_from_filename(local_file)
        return k

    @staticmethod
    def pull_file_from_s3(bucket, local_file, metadata_only=False):
        if not CloudHelper.ENABLED['s3']:
            return None

        key_name = os.path.basename(local_file)
        k = CloudHelper.connect_s3_bucket(bucket).get_key(key_name)
        if (k is not None) and (not metadata_only):
            k.get_contents_to_filename(local_file)
        return k
