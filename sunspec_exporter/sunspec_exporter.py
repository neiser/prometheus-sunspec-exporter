#!/usr/bin/env python3


"""sunspec-prometheus-exporter

Usage:
  sunspec_exporter.py start [ --port PORT ] [ --sunspec_address SUNSPEC_ADDRESS ] [ --filter METRIC_FILTER... ] --sunspec_ip SUNSPEC_IP --sunspec_port SUNSPEC_PORT --sunspec_model_ids MODEL_IDS
  sunspec_exporter.py query [ --sunspec_address SUNSPEC_ADDRESS ] --sunspec_ip SUNSPEC_IP --sunspec_port SUNSPEC_PORT 

Options:
  -h --help                          Show this screen.
  --version                          Show version.
  query                              Dump out current data for analysis; and exit
  start                              Run the prometheus node_exporter
  --port PORT                        Prometheus Client listen port [default: 9807]
  --sunspec_ip SUNSPEC_IP            IP Address of the SunSpec device (Modbus TCP)
  --sunspec_model_ids MODEL_IDS      Comma separated list of the ids of the module you want the data from
  --sunspec_port SUNSPEC_PORT        Modbus port [default: 502]
  --sunspec_address SUNSPEC_ADDRESS  Target modbus device address [default: 1]
  --filter METRICFILTER              Filter the value and alter the reponse. 

Filtering:
  Some devices require filtering of the register values received, due to bugs or features in the hardware.
  This filter support, allows rewriting the exported value, with some basic functions.

  For example, some SMA inverter models return 3276.8 for NaN, for DC Amps, at night time.
  The correct value is 0 when it is not converting energy.

  See https://github.com/inosion/prometheus-sunspec-exporter/blob/main/images/filtering_example_amps_bad.png for an example.

  METRICFILTER is a space separated 3-Tuple, <metric_regex> <function>:<args> <replace_value> 
  
  "Amps_Phase[A-Z]_Aph[A-Z] gt:3276 0"

  Which reads, when the metric matching regex, Amps_Phase[A-Z]_Aph[A-Z] is greater than 3276, set the metric as 0.
  In this example case, the inverter jumps from low 0.4, 0.5 Amps, up to 3276.7 (signed int16 Upper) represented as NaN. 

  The sudden jump, results in out of whack telemetry.

"""

from docopt import docopt
from prometheus_client import start_http_server, Summary
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily, REGISTRY
import sunspec.core.client as client
import sunspec.core.suns as suns
try:
    import xml.etree.ElementTree as ET
except:
    import elementtree.ElementTree as ET
import sunspec.core.pics as pics
from xml.dom import minidom
import logging
import sys
import threading
import time
import re
import collections

log = logging.getLogger('sunspec_exporter')

Filter = collections.namedtuple('Filter', ['regex', 'fn'])
class FnMapping:

    def filter_fn(fn, replacement, *args):
        def filter(v):
            return fn(float(replacement), *map(float, args), v)
        return filter

    def gt(replacement, upper_bound, val):
        if val > upper_bound:
            return replacement
        else:
            return val

    def lt(replacement, lower_bound, val):
        if val < lower_bound:
            return replacement
        else:
            return val

    def gte(replacement, upper_bound, val):
        if val >= upper_bound:
            return replacement
        else:
            return val

    def lte(replacement, lower_bound, val):
        if val <= lower_bound:
            return replacement
        else:
            return val

    def equals(replacement, equals_val, val):
        if val == equals_val:
            return replacement
        else:
            return val

# Create a metric to track time spent and requests made.
REQUEST_TIME = Summary('sunspec_fn_collect_data',
                       'Time spent collecting the data')

# Decorate function with metric.
@REQUEST_TIME.time()
def collect_data(sunspec_client, filters):
    results = {}
    sunspec_client.read()

    for model in sunspec_client.device.models_list:
        for block in model.blocks:
            if block.index > 0:
                index = '%02d_' % (block.index)
            else:
                index = ''
            for point in block.points_list:
                value = to_number(point.value)
                if value is None:
                    continue
                if point.point_type.label:
                    point_label = re.sub('[^a-zA-Z0-9_]', '_', point.point_type.label)
                    metric_label = f"{index}{point_label}_{point.point_type.id}"
                else:
                    metric_label = f"{index}{point.point_type.id}"

                units = point.point_type.units
                if units is not None:
                    unit_label = f"_{units}"
                    metric_type = "Gauge"
                else:
                    unit_label = ""
                    metric_type = "Counter"

                for x in filters:
                    if x.regex.match(metric_label):
                        value = x.fn(value)

                results[f"{metric_label}{unit_label}"] = {"value": value, "metric_type": metric_type}

    return results


def to_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


class SunspecCollector(object):
    "The ip, port and target is of the modbus/sunspec device"

    def __init__(self, model_ids, ip, port, target, filters):
        self.model_ids = model_ids
        self.ip = ip
        self.port = port
        self.target = target
        self.filters = filters
        self.sunspec_client = None
        self.lock = threading.Lock()

    def connect(self):
        sunspec_client = client.SunSpecClientDevice(
            client.TCP, self.target, ipaddr=self.ip, ipport=self.port, timeout=10.0
        )
        # Reading only the requested models reduces the load on the device.
        for model in sunspec_client.device.models_list.copy():
            if str(model.id) not in self.model_ids:
                sunspec_client.device.models_list.remove(model)
        log.info('Connected to %s:%s/%s, collecting models %s', self.ip, self.port, self.target,
                 ', '.join(model_name(m) for m in sunspec_client.device.models_list))
        return sunspec_client

    def disconnect(self):
        try:
            self.sunspec_client.close()
        except Exception:
            pass
        self.sunspec_client = None

    def collect(self):
        with self.lock:
            try:
                if self.sunspec_client is None:
                    self.sunspec_client = self.connect()
                results = collect_data(self.sunspec_client, self.filters)
            except Exception as e:
                log.error('Reading %s:%s/%s failed: %s', self.ip, self.port, self.target, e)
                self.disconnect()
                yield up_metric(0)
                return

        labels = [self.ip, str(self.port), str(self.target)]
        for name, result in results.items():
            if result["metric_type"] == "Counter":
                m = CounterMetricFamily(f"sunspec_{name}", "", labels=["ip", "port", "target"])
            else:
                m = GaugeMetricFamily(f"sunspec_{name}", "", labels=["ip", "port", "target"])
            m.add_metric(labels, result["value"])
            yield m
        yield up_metric(1)


def up_metric(value):
    return GaugeMetricFamily('sunspec_up', 'Whether the last read from the SunSpec device succeeded', value=value)


def model_name(model):
    "Model Data Name"
    if model.model_type.label:
        label = '%s (%s)' % (model.model_type.label, str(model.id))
    else:
        label = '(%s)' % (str(model.id))
    return label

def sunspec_test(ip, port, address):

    """
    Original suns options:

        -o: output mode for data (text, xml)
        -x: export model description (slang, xml)
        -t: transport type: tcp or rtu (default: tcp)
        -a: modbus slave address (default: 1)
        -i: ip address to use for modbus tcp (default: localhost)
        -P: port number for modbus tcp (default: 502)
        -p: serial port for modbus rtu (default: /dev/ttyUSB0)
        -b: baud rate for modbus rtu (default: 9600)
        -T: timeout, in seconds (can be fractional, such as 1.5; default: 2.0)
        -r: number of retries attempted for each modbus read
        -m: specify model file
        -M: specify directory containing model files
        -s: run as a test server
        -I: logger id (for sunspec logger xml output)
        -N: logger id namespace (for sunspec logger xml output, defaults to 'mac')
        -l: limit number of registers requested in a single read (max is 125)
        -c: check models for internal consistency then exit
        -v: verbose level (up to -vvvv for most verbose)
        -V: print current release number and exit
    """


    try:
        sd = client.SunSpecClientDevice(client.TCP, address, ipaddr=ip, ipport=port, timeout=10.0)

    except client.SunSpecClientError as e:
        print('Error: %s' % (e))
        sys.exit(1)

    if sd is not None:

        print( '\nTimestamp: %s' % (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))

        # read all models in the device
        sd.read()

        root = ET.Element(pics.PICS_ROOT)
        sd.device.to_pics(parent = root, single_repeating = True)
        print(minidom.parseString(ET.tostring(root)).toprettyxml(indent="  "))

        for model in sd.device.models_list:
            if model.model_type.label:
                label = '%s (%s)' % (model.model_type.label, str(model.id))
            else:
                label = '(%s)' % (str(model.id))
            print('\nmodel: %s\n' % (label))
            for block in model.blocks:
                if block.index > 0:
                  index = '%02d:' % (block.index)
                else:
                  index = '   '
                for point in block.points_list:
                    if point.value is not None:
                        if point.point_type.label:
                            label = '   %s%s (%s):' % (index, point.point_type.label, point.point_type.id)
                        else:
                            label = '   %s(%s):' % (index, point.point_type.id)
                        units = point.point_type.units
                        if units is None:
                            units = ''
                        if point.point_type.type == suns.SUNS_TYPE_BITFIELD16:
                            value = '0x%04x' % (point.value)
                        elif point.point_type.type == suns.SUNS_TYPE_BITFIELD32:
                            value = '0x%08x' % (point.value)
                        else:
                            value = str(point.value).rstrip('\0')
                        print('%-40s %20s %-10s' % (label, value, str(units)))


if __name__ == '__main__':
    arguments = docopt(__doc__, version='sunspec-prometheus-exporter 1.0')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    sunspec_ip =        arguments["--sunspec_ip"]
    sunspec_port =      int(arguments["--sunspec_port"])
    sunspec_address =   arguments["--sunspec_address"]

    if arguments["query"]:

        sunspec_test(sunspec_ip, sunspec_port, sunspec_address)
        sys.exit(0)


    if arguments["start"]:

        sunspec_model_ids = arguments["--sunspec_model_ids"].split(",")

        filters = []
        if arguments["--filter"] is not None:
            for f in arguments["--filter"]:
                (filter_metric_regex, func_n_params, replacement) = f.split(" ")
                func_name, *parameters = func_n_params.split(":")
                func = FnMapping.filter_fn(getattr(FnMapping, func_name), replacement, *parameters)
                log.info('Added filter %s(%s) [%s] -> %s', func_name, func_n_params, replacement, filter_metric_regex)
                filters.append(Filter(regex=re.compile(filter_metric_regex),fn=func))

        REGISTRY.register(SunspecCollector(
                sunspec_model_ids,
                sunspec_ip,
                sunspec_port,
                sunspec_address,
                filters
            )
        )

        port = int(arguments["--port"])
        start_http_server(port)
        log.info('Serving metrics on port %d for %s:%d/%s, models %s',
                 port, sunspec_ip, sunspec_port, sunspec_address, ','.join(sunspec_model_ids))

        while True:
            time.sleep(1000)
