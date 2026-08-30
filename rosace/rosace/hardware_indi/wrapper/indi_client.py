"""
INDI client wrapper -- shared-singleton connection to an indiserver, BLOB
reception, and generic property-vector handler registration.

Distant heritage: this file originates from the PANOPTES/POCS observatory
control software (github.com/panoptes/POCS, MIT license) -- see
THIRD_PARTY_LICENSES.md at the repository root for the full license text
and which parts of hardware_indi/ this covers.
"""

# Basic stuff
from contextlib import contextmanager
import io
import json
import logging
import multiprocessing
import queue
import threading

# Indi stuff
import PyIndi

# Imaging and Fits stuff
from astropy.io import fits

# Local
from .base import Base
from .indi_web_manager_client import IndiWebManagerClient, IndiWebManagerDummy
from .errors import BLOBError, IndiClientPredicateTimeoutError

logger = logging.getLogger("spectro.indi.indi_client")

defaultTimeout = 30

class BLOB:
    def __init__(self, bp):
        self.name = bp.name
        self.label = bp.label
        self.format = bp.format
        self.blob_len = bp.bloblen
        self.size = bp.size
        self.data = bp.getblobdata()

    def get_fits(self):
        return fits.open(io.BytesIO(self.data))

    def save(self, filename):
        with open(filename, 'wb') as file:
            file.write(self.data)

class BLOBListener(Base):
    def __init__(self, device_name, queue_size=1):
        # Init "our" Base class
        Base.__init__(self)
        self.device_name = device_name
        self.queue = multiprocessing.Queue(maxsize=queue_size)

    def get(self, timeout=300):
        try:
            blob = self.queue.get(True, timeout)
            return blob
        except queue.Empty:
            raise BLOBError(f"Timeout while waiting for BLOB on {self.device.name}")

    def __str__(self):
        return f"BLOBListener(device={self.device_name}"

    def __repr__(self):
        return self.__str__()

class SingletonIndiClientHolder:
    _instances = {}

    def __new__(cls, *args, **kwargs):
        if "config" in kwargs:
            config = kwargs["config"]
        else:
            config = args[0]

        if config.get("use_unique_client", False):
            return object.__new__(cls)

        key = f"{config['indi_host']}:{config['indi_port']}"
        if key not in cls._instances:
            cls._instances[key] = object.__new__(cls)
        return cls._instances[key]

# First inheritance is SingletonIndiClientHolder to ensure call of __new__
class IndiClient(SingletonIndiClientHolder, PyIndi.BaseClient, Base):
    '''
        This Indi Client class can be used as a singleton, so that it can be
        used to interact with multiple devices, instead of declaring one client
        per device to manage.

        Every virtual function instanciated is called asynchronously by an
        external C++ thread, and not by the python main thread, so be careful.
    '''

    def __init__(self, config):
        # Init "our" Base class
        Base.__init__(self)

        # Call indi client base classe ctor
        PyIndi.BaseClient.__init__(self)

        if config is None:
            config = dict(indi_host="localhost",
                          indi_port=7624)
        self.remote_host = config['indi_host']
        self.remote_port = int(config['indi_port'])
        self.setServer(self.remote_host, self.remote_port)
        logger.debug(f"Indi Client, remote host is: {self.getHost()}:{self.getPort()}")

        self.indi_webmanager_client = IndiWebManagerDummy()
        if "indi_webmanager" in config:
            self.indi_webmanager_client = IndiWebManagerClient(config["indi_webmanager"], indi_config=config)

        # Blob related attributes
        self.blob_event = threading.Event()
        self.__blob_listeners = []
        self.queue_size = config.get('queue_size', 1)
        self.__pv_handlers = {}

        # Finished configuring
        logger.debug('Configured Indi Client successfully')

    ###############################################################################
    # <begin=Virtual methods>
    ###############################################################################
    def newDevice(self, d):
        '''Emmited when a new device is created from INDI server.'''
        logger.info(f"new device {d.getDeviceName()}")

    def removeDevice(self, d):
        '''Emmited when a device is deleted from INDI server.'''
        logger.info(f"remove device {d.getDeviceName()}")

    def newProperty(self, p):
        '''Emmited when a new property is created for an INDI driver.'''
        logger.debug(f"new property {p.getName()} as {p.getTypeAsString()} for device {p.getDeviceName()}")

    def updateProperty(self, p):
        '''Emmited when a new property value arrives from INDI server.'''
        if p.getType() == PyIndi.INDI_BLOB:
            self.process_blob_handlers(PyIndi.PropertyBlob(p)[0])
        else:
            self.process_generic_handlers(p)

    def removeProperty(self, p):
        '''Emmited when a property is deleted for an INDI driver.'''
        logger.info(f"remove property {p.getName()} as {p.getTypeAsString()} for device {p.getDeviceName()}")

    def newMessage(self, d, m):
        '''Emmited when a new message arrives from INDI server.'''
        logger.info(f"new Message {d.messageQueue(m)}")

    def serverConnected(self):
        '''Emmited when the server is connected.'''
        logger.info(f"Server connected ({self.getHost()}:{self.getPort()})")

    def serverDisconnected(self, code):
        '''Emmited when the server gets disconnected.'''
        logger.info(f"Server disconnected (exit code = {code},{self.getHost()}:{self.getPort()})")

    ###############################################################################
    # <end=Virtual methods>
    ###############################################################################

    def connect_device(self, device_name):
        self.connectDevice(deviceName=device_name)

    def disconnect_device(self, device_name):
        self.disconnectDevice(deviceName=device_name)

    def disconnect_from_server(self):
        self.disconnectServer()

    def connect_to_server(self, sync=True, timeout=defaultTimeout):
      if self.isServerConnected():
          logger.warning(f"Already connected to server at {self.getHost()}: {self.getPort()}")
      else:
          logger.info(f"Connecting to server at {self.getHost()}: {self.getPort()}")
          if not self.connectServer():
              logger.error(f"No indiserver running on {self.getHost()}:{self.getPort()}")
          else:
              logger.info(f"Successfully connected to server at {self.getHost()}:{self.getPort()}")

    '''
    Indi related stuff (implementing BaseClient methods)
    '''
    def device_names(self):
        return [d.getDeviceName() for d in self.getDevices()]

    def process_blob_handlers(self, bp):
        # this threading.Event is used for sync purpose in other part of the code
        logger.debug(f"new BLOB received: {bp.name}")
        for listener in self.__blob_listeners:
            if bp.bvp.device == listener.device_name:
                blob=BLOB(bp)
                try:
                    listener.queue.put_nowait(blob)
                except queue.Full:
                    listener.queue.get_nowait()
                    listener.queue.put_nowait(blob)

    def process_generic_handlers(self, pv):
        try:
            list(
                map(
                    lambda f: f(pv),
                    self.__pv_handlers[pv.getDeviceName()][pv.getName()].values())
            )
        except KeyError:
            pass

    def reset_blob_listener(self, device_name, queue_size=None):
        queue_size = self.queue_size if queue_size is None else queue_size
        self.remove_blob_listener(device_name)
        blob_listener = BLOBListener(device_name, queue_size)
        self.__blob_listeners.append(blob_listener)
        return blob_listener

    def remove_blob_listener(self, device_name):
        self.__blob_listeners[:] = [el for el in self.__blob_listeners if el.device_name != device_name]

    def add_pv_handler(self, device_name, pv_name, handler_name, pv_handler):
        if device_name not in self.__pv_handlers:
            self.__pv_handlers[device_name] = {pv_name: {handler_name: pv_handler}}
        elif pv_name not in self.__pv_handlers[device_name]:
            self.__pv_handlers[device_name][pv_name] = {handler_name: pv_handler}
        else:
             self.__pv_handlers[device_name][pv_name][handler_name] = pv_handler

    def remove_pv_handler(self, device_name, pv_name=None, handler_name=None):
        # if no property vector is provided, remove all handler for this device
        if pv_name is None:
            self.__pv_handlers.pop(device_name, None)
        # if no handler name is provided, remove all handlers for this property vector
        elif handler_name is None:
            if device_name in self.__pv_handlers:
                self.__pv_handlers[device_name].pop(pv_name, None)
        else:
            if device_name in self.__pv_handlers:
                if handler_name in self.__pv_handlers[device_name]:
                    self.__pv_handlers[device_name][pv_name].pop(handler_name, None)

            self.__pv_handlers[device_name][pv_name].pop(handler_name, None)

    def __str__(self):
        return f"INDI client connected to {self.remote_host}:{self.remote_port}"

    def __repr__(self):
        return f"INDI client connected to {self.remote_host}:{self.remote_port}"

    def enable_blob(self, blob_mode=PyIndi.B_ALSO, device_name=None, property_name=None):
        r"""
        Sends a signal to the server that tells it, that this client wants to receive L{indiblob} objects.
        If this method is not called, the server will not send any L{indiblob}. The DCD clients calls it each time
        an L{indiblob} is defined.
        From https://github.com/indilib/indi/blob/master/libs/indiabstractclient/abstractbaseclient.h#L165C26-L165C38
         *  Set the BLOB handling mode for the client. The client may either receive:
         *  <ul>
         *    <li>Only BLOBS</li>
         *    <li>BLOBs mixed with normal messages</li>
         *    <li>Normal messages only, no BLOBs</li>
         *  </ul>
         *  If \e dev and \e prop are supplied, then the BLOB handling policy is set for this particular device and property.
         *  if \e prop is NULL, then the BLOB policy applies to the whole device.
         *
         *  @param blobH BLOB handling policy
         *  @param dev name of device, required.
         *  @param prop name of property, optional.

        B_ALSO = 1
        B_NEVER = 0
        B_ONLY = 2

        @return: B{None}
        @rtype: NoneType
        """
        self.setBLOBMode(blob_mode, device_name, property_name)
