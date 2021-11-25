"""
GP socket communication class.
Contains the python client side to operate the (Julia) GP module, for XRD and
with composition spread for binary systems
"""
from crccheck.crc import Crc32, CrcXmodem
from crccheck.checksum import Checksum32

import logging
import logging.config
import yaml
import zlib
import binascii
import socket
import struct
import sys
import io
import time
import numpy as np
import copy as cp



with open('logging.yaml', 'r') as f:
    log_cfg = yaml.safe_load(f.read())
logging.config.dictConfig(log_cfg)

class ClientSocket:
    """
    Contains the socket objects for basic operations:
    - open a socket
    - close a socket
    """
    @classmethod
    def __init_subclass__(cls, connect = True, address = None, port = None, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self):
        self.logger = logging.getLogger("ClientSocket")
        self.logger.setLevel(logging.INFO)
        self.sock = None
        self.bufflen = 4096 #Buffer length for large data transfers             
        self.gp_addresses()

    def open_socket(self, address, port):
        """
        Open a TCP/IP socket
        """
        self.address = address
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_address = (address, port)
        self.sock.settimeout(30)
        self.sock.setblocking(True)
        try:
            self.logger.info('Opening socket. Addr: %s, Port: %d', self.server_address[0], self.server_address[1])
            self.sock.connect(self.server_address)
            self.logger.info('Opened socket. Addr: %s, Port: %d', self.server_address[0], self.server_address[1])
            self.sock.settimeout(30)
            self.sock.setblocking(True)
            return 0
        except:
            self.logger.error('Openening socket timed out: %s, Port: %d', self.server_address[0], self.server_address[1])
            return 1
        
    def close_socket(self):
        """
        Close a TCP/IP socket
        """
        self.sock.close()
        self.logger.info('Closed socket. Addr: %s, Port: %d', self.server_address[0], self.server_address[1])

    def gp_addresses(self):
        """
        Returns a dictionary of default ports and addresses
        """
        self.ports = {}
        self.ports["gp"]       = 2300
        
        self.addresses = {}
        self.addresses["LSA"] = "128.253.000.74"
        self.addresses["Analysis"] = "128.253.000.71"
        self.addresses["Local"] = socket.gethostname()
        return 

    def autoconnect(self, connect, address, port):
        """
        Select appropriate address and connect, if possible
        """
        if address in self.addresses:
            address_connect = self.addresses[address]
        else:
            address_connect = address
        if port in self.ports:
            if address == "Local":
                self.ports[port] += 1000
            port_connect = self.ports[port]
        else:
            port_connect = port
        if connect:
            success = self.open_socket(address_connect, port_connect)
        self.address = address_connect
        self.port = port_connect
        return success

class ClientStructProtocol(ClientSocket):
    """
    General structure-based protocol class. 
    Relies on ClientSocket class.
    This class is used to communicate with cameras, spectrometers, XRD, etc.
    and is suited to exchange large amount of data.
    """
    @classmethod
    def __init_subclass__(cls, connect = True, address = None, port = None, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self):
        super(ClientStructProtocol, self).__init__()
        self.logger = logging.getLogger("ClientStruct")
        self.crc = True

    def comm_structure_format(self):
        """
        Returns the communication structure format, 
        containing 5 integers of length 32, big endian (if CRC is included),
        and 4 integers without CRC
        The default communication method to send requests.
        0: msg, the message index
        1: msgid, tag to track messages
        2: option, single parameter for enhancing some msg
        3: rc, return code, zero on success, other values depending on request task
        4: data_size, length of data send in following send requests
        5: crc32 (optional), CRC32 checksum of the data to be transmitted in subsequent comm.
        """
        if self.crc:
            s_struct = ">I i i i I I"
        else:
            s_struct = ">I i i i I"
        return s_struct, struct.Struct(s_struct)

    def get_crc(self, data):
        """
        Returns crc32 of the data package
        """
        crc32 = zlib.crc32(data)  & 0xFFFFFFFF 
        return crc32
 
    def pack_data(self, packer, msg):
        """
        Returns the general packed message based on packer.
        """
        values = tuple(msg)
        packed_data = packer.pack(*values)
        return packed_data, values

    def unpack_data(self, unpacker, packed_data):
        """
        Returns the general unpacked message based on unpacker
        """
        unpacked_data = unpacker.unpack(packed_data)
        msg = list(unpacked_data[:])
        return msg

    def comm_pack_data(self, packer, msg):
        """
        Returns the packed message based on packer specifically for the
        communication protocol.
        """
        if len(msg) < 5 or len(msg) > 6:
            self.logger.error("Length of comm message not between 5 and 6, but %d", len(msg))
        values = tuple(msg)
        packed_data = packer.pack(*values)
        return packed_data, values

    def comm_unpack_data(self, unpacker, packed_data):
        """
        Returns the unpacked message based on unpacker specifically for the
        communication protocol.
        """
        unpacked_data = unpacker.unpack(packed_data)
        msg = list(unpacked_data[:])
        return msg

    def string_packer_format(self, length):
        """
        Structure format of a string of given length 
        Returns the structure format and the structure itself.
        """
        s_struct = ">" + str(length) + "s"
        return s_struct, struct.Struct(s_struct)

    def comm_send_struct(self, msg_out):
        """
        Sends communication data.
        Wrapper to send data based specifically for the communication protocol.
        """
        if self.crc:
            if len(msg_out) != 6:
                self.logger.error("Message length for Struct comm must be exactly 6, but %d", len(msg_out))
                return
        else:
            if len(msg_out) != 5:
                self.logger.error("Message length for Struct comm must be exactly 5, but %d", len(msg_out))
                return

        s_struct, packer = self.comm_structure_format()
        packed_data, values = self.comm_pack_data(packer, msg_out)
        
        # Send data
        self.logger.info("Sending "+':'.join([str(i) for i in msg_out]))
        self.logger.debug("Sending %s", binascii.hexlify(packed_data))
        self.sock.sendall(packed_data)

    def comm_recv_struct(self, msg_out):
        """
        Receivs communication data and compares with the initial 
        outgoing message (msg_out) for any errors.
        Wrapper to receive data based specifically for the communication
        protocol.
        Returns the received message.
        """
        if self.crc:
            if len(msg_out) != 6:
                self.logger.error("Message length for Struct comm must be exactly 6, but %d", len(msg_out))
                return
        else:
            if len(msg_out) != 5:
                self.logger.error("Message length for Struct comm must be exactly 5, but %d", len(msg_out))
                return

        s_struct, unpacker = self.comm_structure_format()
        packed_data = self.sock.recv(unpacker.size)
        msg_in = self.comm_unpack_data(unpacker, packed_data)
        self.logger.info("Received "+':'.join([str(i) for i in msg_in]))
        self.logger.debug("Received %s", binascii.hexlify(packed_data))

        #Check message for consistency
        if msg_in[0] != msg_out[0] or msg_in[1] != msg_out[1]:
            self.logger.error("Reply inconsistency on Struct comm: %d:%d, %d:%d", msg_in[0], msg_out[0], msg_in[1], msg_out[1])
            return
        if msg_in[0] != 1 and msg_in[3] != msg_out[3]:
            self.logger.warning("RC value returned non-zero %d, %d:%d", msg_in[0], msg_in[3], msg_out[3])
        return msg_in

    def recv_data_buffered(self, unpacker, msg_recv):
        """
        For receiving large amount of data, use this
        buffered transfer protocol
        """ 
        if msg_recv[4] != unpacker.size:
            self.logger.error("Unpacker size does not match the expected data size %d:%d", msg_recv[4], unpacker.size)
            return
        self.logger.info("Receiving first block")

        packed_data = bytearray(unpacker.size)
        pos = 0
        while pos < unpacker.size:
            cr = self.sock.recv_into(memoryview(packed_data)[pos:])
            if cr == 0:
                raise EOFError
            pos += cr
        amount_received = pos
        ##packed_data = self.sock.recv(min(self.bufflen, unpacker.size))
        ##print(type(packed_data))
        ##self.logger.debug("Received %s", binascii.hexlify(packed_data))
        ##amount_received = len(packed_data)
        ##while amount_received < unpacker.size:
        ##    self.logger.info("Block no x")
        ##    packed_data += self.sock.recv(min(self.bufflen, unpacker.size - amount_received))
        ##    amount_received = len(packed_data)
        ###print("Amount of data expected/received", unpacker.size, amount_received)
        self.logger.info("Received last block")
        if unpacker.size != amount_received:
            self.logger.error("Data loss during transfer of wavelength data %d:%d", unpacker.size, amount_received)
            return
        self.check_crc(packed_data, msg_recv)
        data_recv = self.unpack_data(unpacker, packed_data)
        return data_recv

    def check_msgid(self, msg_id, msg_recv):
        consistent = True
        if msg_id != msg_recv[1]:
            self.logger.error("Message ID inconsistency, %d %d", msg_id, msg_recv[1])
            consistent = False
        return consistent

    def check_crc(self, packed_data, msg_recv):
        consistent = True
        if self.crc:
            crc = self.get_crc(packed_data)
            if crc != msg_recv[5]:
                self.logger.warning("CRC mismatch %d:%d", crc, msg_recv[5])
                consistent = False
        return consistent

class ClientGPProtocol(ClientStructProtocol):
    """
    Subclass with functions implemented specifically for the Focus module.
    Relies on the ClientStructProtocol class.
    """
    def __init__(self, connect = True, address = None, port = None):
        super(ClientGPProtocol, self).__init__()
        self.single_comp = True
        self.logger = logging.getLogger("ClientGP")
        if address is None:
            address = self.addresses["Analysis"] 
        if port is None:
            port = "gp"
        self.success = self.autoconnect(connect, address, port)
        if self.success != 0:
            self.logger.error('Autoconnect failed')

    def get_GP_QUERY_VERSION(self, msg_id):
        """
        Returns version of the focus server.
        """
        msg = [1, msg_id, 0, 0, 0]
        if self.crc:
           msg.append(0)
        self.comm_send_struct(msg)
        msg_recv = self.comm_recv_struct(msg)
        if not self.check_msgid(msg_id, msg_recv): return
        self.logger.debug("Server version %d", msg_recv[3])
        return msg_recv[3]

    def GP_SETUP_structure_format(self, nknown):
        """ 
        For setting up a new calculation, or for restart
        nknown is the array size for a restart calculation, important for
        fillin the arrays
        self.gradient_all = np.array([])
        self.gradient_uncert_all = ([])
        self.conditions_all = []
        nknown should be multiples of self.input_parameters["nout"]
        if nknown is negative, only the data is sent without changing the
        parameters of length abs(nknown)
        """ 
        # set up temperature profile
        s_struct  = "<"
        if nknown >= 0:
            s_struct += "d " # self.input_parameters["dT"] = 25 # uncertainty in peak temperature in C
            s_struct += "d " # self.input_parameters["dx"] = 50/1000 # uncertainty in position in mm
            # define the range of temperatures that are taken from each stripe
            s_struct += "I " # self.input_parameters["c_min"] = 10
            s_struct += "I " # self.input_parameters["c_max"] = 200
            s_struct += "I " # self.input_parameters["nout"] = 16
            # σAI
            # stripe uncertainty acquisition function
            s_struct += "d " # self.input_parameters["T_length"] = 30.
            s_struct += "d " # self.input_parameters["tau_length"] = .3
            s_struct += "d " # self.input_parameters["c_length"] = .1 # disregard for systems without composition dimension
            # Dimensions to be saved from the known arrays for a restart
            # Only add this if restart, i.e., nknown > 0
        if abs(nknown) > 0:
            nnknown = abs(nknown)
            s_struct += str(nnknown) + "d " #gradient_all
            s_struct += str(nnknown) + "d " #gradient_uncert_all
            if self.single_comp:
                s_struct += str(2*nnknown) + "d " #conditions_all
            else:
                s_struct += str(3*nnknown) + "d " #conditions_all
        return s_struct, struct.Struct(s_struct)

    def GP_SETUP_structure_todict(self, s_data, nknown):
        """
        Returns the dict from the GP_SETUP_structure_format
        """
        s_dict = {}
        pp = 0
        if nknown >= 0:
            s_dict["dT"] = s_data[pp];          pp += 1
            s_dict["dx"] = s_data[pp];          pp += 1
            s_dict["c_min"] = s_data[pp];       pp += 1
            s_dict["c_max"] = s_data[pp];       pp += 1
            s_dict["nout"] = s_data[pp];        pp += 1
            s_dict["T_length"] = s_data[pp];    pp += 1
            s_dict["tau_length"] = s_data[pp];  pp += 1
            s_dict["c_length"] = s_data[pp];    pp += 1
        if abs(nknown) > 0:
            nnknown = abs(nknown)
            s_dict["gradient_all"] = np.array(s_data[pp:pp+nnknown]);        pp += nnknown
            s_dict["gradient_uncert_all"] = np.array(s_data[pp:pp+nnknown]); pp += nnknown
            if self.single_comp:
                t_data = s_data[pp:pp+nnknown*2]
                it = iter(t_data)
                # zip the iterator with itself
                s_dict["conditions_all"] = list(zip(it, it))
            else:
                t_data = s_data[pp:pp+nnknown*3]
                it = iter(t_data)
                # zip the iterator with itself
                s_dict["conditions_all"] = list(zip(it, it, it))
        return s_dict

    def GP_SETUP_structure_tolist(self, s_dict, nknown):
        """
        Returns the list from the GP_XRD_TO_GLOBAL_structure_todict dictionary
        """
        s_data = []
        if nknown >= 0:
            s_data.append(s_dict["dT"] )
            s_data.append(s_dict["dx"] )
            s_data.append(s_dict["c_min"]) 
            s_data.append(s_dict["c_max"]) 
            s_data.append(s_dict["nout"]) 
            s_data.append(s_dict["T_length"]) 
            s_data.append(s_dict["tau_length"]) 
            s_data.append(s_dict["c_length"]) 
        if abs(nknown) > 0:
            s_data += s_dict["gradient_all"].tolist()
            s_data += s_dict["gradient_uncert_all"].tolist()
            s_data += [e for l in s_dict["conditions_all"] for e in l]
        return s_data

    def set_GP_SETUP(self, msg_id, nknown, data_dict):
        """
        Sets up or resets the GP, including restart with nknown data points
        """
        msg = [3, msg_id, 0, 0, 0]
        msg[2] = nknown
        # Prepare data
        s_struct, packer = self.GP_SETUP_structure_format(nknown)
        print(data_dict)
        data_list = self.GP_SETUP_structure_tolist(data_dict, nknown)
        print(data_list)
        packed_data, values = self.pack_data(packer, data_list)
        print("Data packed")
        msg[4] = packer.size
        if self.crc:
            crc = self.get_crc(packed_data)
            msg.append(crc) 
        self.comm_send_struct(msg)
        self.sock.sendall(packed_data)
        #Get response
        msg_recv = self.comm_recv_struct(msg)
        if not self.check_msgid(msg_id, msg_recv): return
        if int(msg_recv[3]) != 0:
            self.logger.error("Failed to set GP_SETUP data, %d", msg_recv[3])
            return
        return msg_recv[3]

    def GP_XRD_TO_GLOBAL_DIM_structure_format(self):
        """
        Returns the structure format of the array dimensions of the inner
        loop
        """
        s_struct  = "<"
        s_struct += "I "    #Size of x (number of positions)
        s_struct += "I "    #Number of Qvalues (for the Y matrix)
        return s_struct, struct.Struct(s_struct)

    def set_GP_XRD_TO_GLOBAL_DIM(self, msg_id, ndims):
        """
        Sends the inner loop dims and keeps them in self
        ndims[0] is the number of positions
        ndims[1] is the number of Q values
        """
        msg = [4, msg_id, 0, 0, 0]
        s_struct, packer = self.GP_XRD_TO_GLOBAL_DIM_structure_format()
        packed_data, values = self.pack_data(packer, ndims)
        if self.crc:
            crc = self.get_crc(packed_data)
            msg.append(crc)
        self.comm_send_struct(msg)
        self.sock.sendall(packed_data)
        msg_recv = self.comm_recv_struct(msg)
        if not self.check_msgid(msg_id, msg_recv): return
        if int(msg_recv[3]) != 0:
            self.logger.error("Failed to set ndims, %d", msg_recv[3])
            return
        return msg_recv[3]

    def GP_XRD_TO_GLOBAL_structure_format(self, ndim):
        """                                 
        Returns the structure format of sending the XRD maps
        """                                 
        s_struct  = "<"                     
        s_struct += str(ndim[0]) + "d "            #The list of positions x
        s_struct += str(ndim[0] * ndim[1]) + "d "  #The size of the complete xrd map, x times number of Qvalues
        s_struct += str(ndim[1]) + "d "            #Qvalues
        s_struct += "d "                           #T_max
        s_struct += "d "                           #log10_tau
        s_struct += "d "                           #comp
        #Could add bias parameters after
        return s_struct, struct.Struct(s_struct)

    def GP_XRD_TO_GLOBAL_structure_todict(self, s_data, ndims):
        """
        Returns the dict from the GP_XRD_TO_GLOBAL_structure_format
        """
        s_dict = {}
        pp = 0
        s_dict["x"] = np.array(s_data[pp:pp+ndims[0]])
        pp += ndims[0]
        Yin = []
        s_dict["Y"] = np.array(s_data[pp:pp+ndims[0] * ndims[1]]).reshape((ndims[1], ndims[0]))
        for i in range(ndims[1]):
            Yin.append(s_dict["Y"][i,:])
        s_dict["Y"] = np.array(Yin) #Or keep it as list?
        pp += ndims[0] * ndims[1]
        s_dict["Qval"] = np.array(s_data[pp:pp+ndims[1]])
        pp += ndims[1]
        s_dict["T_max"] = s_data[pp]
        pp += 1
        s_dict["log10_tau"] = s_data[pp]
        pp += 1
        s_dict["comp"] = s_data[pp]
#        pp += 1
#        bias_parameters = []
#        for i in range(ndims[2]):
#            bb = (s_data[pp], s_data[pp+1], s_data[pp+2], s_data[pp+3])
#            bias_parameters.append(bb)
#            pp += 4
#        s_dict["rescale"] = bias_parameters
#        s_dict["grid"] = np.array(s_data[pp:pp + ndims[3]])
#        pp += ndims[3]
#        #s_dict["num_next"] = s_data[pp]
        return s_dict

    def GP_XRD_TO_GLOBAL_structure_tolist(self, s_dict):
        """
        Returns the list from the GP_XRD_TO_GLOBAL_structure_todict dictionary
        """
        s_data = []
        s_data += s_dict["x"].tolist()
        s_data += s_dict["Y"].flatten().tolist()
        s_data += s_dict["Qval"].tolist()
        s_data.append(s_dict["T_max"])
        s_data.append(s_dict["log10_tau"])
        s_data.append(s_dict["comp"])
        ##for r in s_dict["rescale"]:
        ##    s_data += (list(r))
        ##s_data += s_dict["grid"].tolist()
        ###s_data.append(s_dict["num_next"])
        return s_data

    def GP_ALL_structure_format(self, nknown):
        """ 
        self.gradient_all = np.array([])
        self.gradient_uncert_all = ([])
        self.conditions_all = []
        nknown should be multiples of self.input_parameters["nout"]
        """ 
        # set up temperature profile
        s_struct  = "<"
        if nknown > 0:
            s_struct += str(nknown) + "d " #gradient_all
            s_struct += str(nknown) + "d " #gradient_uncert_all
            if self.single_comp:
                s_struct += str(2*nknown) + "d " #conditions_all
            else:
                s_struct += str(3*nknown) + "d " #conditions_all
        return s_struct, struct.Struct(s_struct)

    def set_GP_XRD_TO_GLOBAL(self, msg_id, ndims, data_dict):
        """
        Sends the positions, the xrd map, T_max, and log10_tau
        Computes the stripe GP and stores the output in self
        ndims[0] is the number of positions
        ndims[1] is the number of Q values
        """
        msg = [5, msg_id, 0, 0, 0]
        # Prepare data, it should be ndims = xrd_map.shape.tolist()
        s_struct, packer = self.GP_XRD_TO_GLOBAL_structure_format(ndims)
        data_list = self.GP_XRD_TO_GLOBAL_structure_tolist(data_dict)
        packed_data, values = self.pack_data(packer, data_list)
        msg[4] = packer.size
        if self.crc:
            crc = self.get_crc(packed_data)
            msg.append(crc) 
        self.comm_send_struct(msg)
        self.sock.sendall(packed_data)
        #Get response
        msg_recv = self.comm_recv_struct(msg)
        if not self.check_msgid(msg_id, msg_recv): return
        self.logger.info("Expecting XRD_TO_GLOBAL data, %d", msg_recv[3])
        nknown = msg_recv[2]
        s_struct, unpacker = self.GP_ALL_structure_format(nknown)
        data_recv = self.recv_data_buffered(unpacker, msg_recv)
        pp = 0
        s_dict = {}
        s_dict["gradient_all"] = np.array(data_recv[pp:pp+nknown]);         pp += nknown
        s_dict["gradient_uncert_all"] = np.array(data_recv[pp:pp+nknown]);  pp += nknown
        if self.single_comp:
            t_data = data_recv[pp:pp+nknown*2]
            it = iter(t_data)
            # zip the iterator with itself
            s_dict["conditions_all"] = list(zip(it, it))
        else:
            t_data = data_recv[pp:pp+nknown*3]
            it = iter(t_data)
            # zip the iterator with itself
            s_dict["conditions_all"] = list(zip(it, it, it))
        return msg_recv[3], s_dict
        
    def GP_COND_structure_format(self, ncond):
        """                                 
        Returns the structure format of sending the XRD maps
        """                                 
        s_struct  = "<"                     
        if self.single_comp:
            s_struct += str(ncond * 2) + "d "  #2 times the number of counditions, which is a touple of T_max, log10_tau, and comp
        else:
            s_struct += str(ncond * 3) + "d "  #3 times the number of counditions, which is a touple of T_max, log10_tau, and comp
        return s_struct, struct.Struct(s_struct)

    def set_GP_ALLOWED_COND(self, msg_id, conds):
        """
        Send the list of touples of allowed conditions
        """
        msg = [6, msg_id, 0, 0, 0]
        nconds = len(conds) #list of conditions, a touple of parameters
        msg[2] = nconds
        s_struct, packer = self.GP_COND_structure_format(nconds)
        data_list = [e for l in conds for e in l]
        packed_data, values = self.pack_data(packer, data_list)
        msg[4] = packer.size
        if self.crc:
            crc = self.get_crc(packed_data)
            msg.append(crc) 
        self.comm_send_struct(msg)
        self.sock.sendall(packed_data)
        #Get response
        msg_recv = self.comm_recv_struct(msg)
        if not self.check_msgid(msg_id, msg_recv): return
        if int(msg_recv[3]) != 0:
            self.logger.error("Failed to set GP_ALLOWED_COND data, %d", msg_recv[3])
            return
        return msg_recv[3]

    def get_GP_NEXT_COND(self, msg_id, conds, next_n_conds):
        """
        Return a list of next conditions
        """
        msg = [7, msg_id, 0, 0, 0]
        msg[2] = next_n_conds #The number of next conditions requested, only 1 for now
        if self.crc:
           msg.append(0)
        self.comm_send_struct(msg)
        #Get response
        msg_recv = self.comm_recv_struct(msg)
        if not self.check_msgid(msg_id, msg_recv): return

        if self.single_comp:
            if int(msg_recv[3]) != next_n_conds * 2:
                self.logger.error("Failed to get GP_NEXT_COND , %d", msg_recv[3])
                return
        else:
            if int(msg_recv[3]) != next_n_conds * 3:
                self.logger.error("Failed to get GP_NEXT_COND , %d", msg_recv[3])
                return
        #Receive the structure data
        s_struct, unpacker = self.GP_COND_structure_format(next_n_conds)
        data_recv = self.recv_data_buffered(unpacker, msg_recv)
        it = iter(data_recv)
        # zip the iterator with itself
        if self.single_comp:
            next_conditions = list(zip(it, it))
        else:
            next_conditions = list(zip(it, it, it))
        return next_conditions 

    def GP_EXCLUDE_STRIPES_structure_format(self, nstripes):
        """                                 
        Returns the structure format of sending the XRD maps
        """                                 
        s_struct  = "<"                     
        s_struct += str(nstripes) + "? "  #number of counditions already annealed
        return s_struct, struct.Struct(s_struct)

    def set_GP_EXCLUDE_STRIPES(self, msg_id, exclude):
        """
        Send list of boolians to exclude stripe. False to exclude, True to
        include
        """
        msg = [8, msg_id, 0, 0, 0]
        nstripes = len(exclude) #list of boolians to include/exclude
        msg[2] = nstripes
        s_struct, packer = self.GP_EXCLUDE_STRIPES_structure_format(nstripes)
        data_list = exclude
        packed_data, values = self.pack_data(packer, data_list)
        msg[4] = packer.size
        if self.crc:
            crc = self.get_crc(packed_data)
            msg.append(crc) 
        self.comm_send_struct(msg)
        self.sock.sendall(packed_data)
        #Get response
        msg_recv = self.comm_recv_struct(msg)
        if not self.check_msgid(msg_id, msg_recv): return
        if int(msg_recv[3]) != 0:
            self.logger.error("Failed to set GP_EXCLUDE_STRIPES data, %d", msg_recv[3])
            return
        return msg_recv[3]

    def GP_EVALUATE_set_structure_format(self, nconds):
        """                                 
        Returns the structure format of sending the evaluation list
        """                                 
        s_struct  = "<"                     
        if self.single_comp:
            s_struct += str(nconds * 2) + "d "  #2 times the number of counditions, which is a touple of T_max, log10_tau
        else:
            s_struct += str(nconds * 3) + "d "  #3 times the number of counditions, which is a touple of T_max, log10_tau, and comp
        return s_struct, struct.Struct(s_struct)

    def GP_EVALUATE_get_structure_format(self, nconds):
        """                                 
        Returns the structure format of sending the means and stds
        """                                 
        s_struct  = "<"                     
        s_struct += str(nconds * 2) + "d "  #2 times the number of counditions, which is a touple of T_max, log10_tau
        return s_struct, struct.Struct(s_struct)

    def get_GP_EVALUATE(self, msg_id, conds):
        """
        Send list of touples of conditions, and return the GP mean and std-dev
        at these conditions
        """
        msg = [9, msg_id, 0, 0, 0]
        nconds = len(conds) #list of boolians to include/exclude
        msg[2] =  nconds
        print("Length of conditions to evaluate", nconds)
        s_struct, packer = self.GP_EVALUATE_set_structure_format(nconds)
        data_list = [e for l in conds for e in l]
        print("Data list", data_list)
        packed_data, values = self.pack_data(packer, data_list)
        msg[4] = packer.size
        if self.crc:
            crc = self.get_crc(packed_data)
            msg.append(crc) 
        self.comm_send_struct(msg)
        self.sock.sendall(packed_data)
        #Get response
        msg_recv = self.comm_recv_struct(msg)
        #Get response
        if not self.check_msgid(msg_id, msg_recv): return
        #Receive the structure data
        s_struct, unpacker = self.GP_EVALUATE_get_structure_format(nconds)
        data_recv = self.recv_data_buffered(unpacker, msg_recv)
        #First half is mean, he second half is std dev
        mean = data_recv[:nconds]
        std  = data_recv[nconds:]
        return mean, std



#---------------------------------------------------------------------------

def test_all_functions():
    with open('logging.yaml', 'r') as f:
        log_cfg = yaml.safe_load(f.read())
    logging.config.dictConfig(log_cfg)
    msg_id = 101
    #address = "Local"
    address = "localhost"
    gp_client = ClientGPProtocol(connect = True, address = address)
    if gp_client.success != 0:
        sys.exit()

    #try:
    recv = gp_client.get_GP_QUERY_VERSION(msg_id)
    print(recv)
    input_parameters = {}
    input_parameters["dT"] = 25 # uncertainty in peak temperature in C
    input_parameters["dx"] = 50/1000 # uncertainty in position in mm
    # define the range of temperatures that are taken from each stripe
    input_parameters["c_min"] = 10
    input_parameters["c_max"] = 200
    input_parameters["nout"] = 16
    # σAI
    # stripe uncertainty acquisition function
    input_parameters["T_length"] = 30.
    input_parameters["tau_length"] = .3
    input_parameters["c_length"] = .1 # disregard for systems without composition dimension
    nknown = 0
    #Setting up new run
    recv = gp_client.set_GP_SETUP(msg_id, nknown, input_parameters)
    #Create some fake data
    ndims = [101,100]
    data_map = []
    for n in range(ndims[0]):
        data_map.append(np.random.rand(ndims[1]))
    data_map = np.array(data_map)
    recv = gp_client.set_GP_XRD_TO_GLOBAL_DIM(msg_id, ndims)
    exclude = [True] * 2
    recv = gp_client.set_GP_EXCLUDE_STRIPES(msg_id, exclude)

    data = {}
    data["x"] = np.linspace(-1.,1.,ndims[0])
    data["Y"] = data_map
    data["Qval"] = np.linspace(1.,100.,ndims[1])
    data["T_max"] = 1000.
    data["log10_tau"] = 3.
    data["comp"] = 0.5
    recv = gp_client.set_GP_XRD_TO_GLOBAL(msg_id, ndims, data)

    conds = []
    ##test for binaries
    #for T, tau, c in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10), np.linspace(0,1,10)):
    #    conds.append((T, tau, c))
    #test for unaries
    for T, tau in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10)):
        conds.append((T, tau))
    recv = gp_client.set_GP_ALLOWED_COND(msg_id, conds)
    next_ncond = 1
    recv_cond = gp_client.get_GP_NEXT_COND(msg_id, conds, next_ncond)
    recv_cond = recv_cond[0]
    print("Next cond", recv_cond)

    #Create some fake data
    ndims = [101,100]
    data_map = []
    for n in range(ndims[0]):
        data_map.append(np.random.rand(ndims[1]))
    data_map = np.array(data_map)
    recv = gp_client.set_GP_XRD_TO_GLOBAL_DIM(msg_id, ndims)

    data = {}
    data["x"] = np.linspace(-1.,1.,ndims[0])
    data["Y"] = data_map
    data["Qval"] = np.linspace(1.,100.,ndims[1])
    data["T_max"] = recv_cond[0]
    data["log10_tau"] = recv_cond[1]
    ##test for binaries
    #data["comp"] = recv_cond[2]
    data["comp"] = 0.
    recv, status_dict = gp_client.set_GP_XRD_TO_GLOBAL(msg_id, ndims, data)

    #Create some fake data
    ndims = [101,100]
    data_map = []
    for n in range(ndims[0]):
        data_map.append(np.random.rand(ndims[1]))
    data_map = np.array(data_map)
    recv = gp_client.set_GP_XRD_TO_GLOBAL_DIM(msg_id, ndims)

    data = {}
    data["x"] = np.linspace(-1.,1.,ndims[0])
    data["Y"] = data_map
    data["Qval"] = np.linspace(1.,100.,ndims[1])
    data["T_max"] = recv_cond[0]
    data["log10_tau"] = recv_cond[1]
    ##test for binaries
    #data["comp"] = recv_cond[2]
    data["comp"] = 0.
    recv, status_dict = gp_client.set_GP_XRD_TO_GLOBAL(msg_id, ndims, data)
    conds = []
    ##test for binaries
    #for T, tau, c in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10), np.linspace(0,1,10)):
    #    conds.append((T, tau, c))
    #test for unaries
    for T, tau in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10)):
        conds.append((T, tau))
    recv = gp_client.set_GP_ALLOWED_COND(msg_id, conds)
    exclude = [True, False, True]
    recv = gp_client.set_GP_EXCLUDE_STRIPES(msg_id, exclude)
    next_ncond = 1
    recv = gp_client.get_GP_NEXT_COND(msg_id, conds, next_ncond)
    print("Next cond", recv)


    #Evaluate
    eval_cond = [[1., 1.], [0.5, 0.2]]
    mean, std = gp_client.get_GP_EVALUATE(msg_id, eval_cond)
    print("Mean, std", mean, std)



    input_parameters = {}
    input_parameters["dT"] = 25 # uncertainty in peak temperature in C
    input_parameters["dx"] = 50/1000 # uncertainty in position in mm
    # define the range of temperatures that are taken from each stripe
    input_parameters["c_min"] = 10
    input_parameters["c_max"] = 200
    input_parameters["nout"] = 16
    # σAI
    # stripe uncertainty acquisition function
    input_parameters["T_length"] = 30.
    input_parameters["tau_length"] = .3
    input_parameters["c_length"] = .1 # disregard for systems without composition dimension
    nknown = 0
    #Setting up new run
    recv = gp_client.set_GP_SETUP(msg_id, nknown, input_parameters)
    recv = gp_client.set_GP_XRD_TO_GLOBAL(msg_id, ndims, data)
    conds = []
    ##test for binaries
    #for T, tau, c in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10), np.linspace(0,1,10)):
    #    conds.append((T, tau, c))
    #test for unaries
    for T, tau in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10)):
        conds.append((T, tau))
    recv = gp_client.set_GP_ALLOWED_COND(msg_id, conds)
    next_ncond = 1
    recv_cond = gp_client.get_GP_NEXT_COND(msg_id, conds, next_ncond)
    recv_cond = recv_cond[0]
    print("Next cond", recv_cond)



    input_parameters["gradient_all"] = status_dict["gradient_all"]
    input_parameters["gradient_uncert_all"] = status_dict["gradient_uncert_all"]
    input_parameters["conditions_all"] = status_dict["conditions_all"]
    nknown = len(input_parameters["conditions_all"])
    #Setting up new run
    recv = gp_client.set_GP_SETUP(msg_id, nknown, input_parameters)

    recv = gp_client.set_GP_XRD_TO_GLOBAL(msg_id, ndims, data)
    conds = []
    ##test for binaries
    #for T, tau, c in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10), np.linspace(0,1,10)):
    #    conds.append((T, tau, c))
    #test for unaries
    for T, tau in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10)):
        conds.append((T, tau))
    recv = gp_client.set_GP_ALLOWED_COND(msg_id, conds)
    next_ncond = 1
    recv_cond = gp_client.get_GP_NEXT_COND(msg_id, conds, next_ncond)
    recv_cond = recv_cond[0]
    print("Next cond", recv_cond)
    input_parameters = {}
    input_parameters["gradient_all"] = status_dict["gradient_all"]
    input_parameters["gradient_uncert_all"] = status_dict["gradient_uncert_all"]
    input_parameters["conditions_all"] = status_dict["conditions_all"]
    nknown = -len(input_parameters["conditions_all"])
    #Setting up new run
    recv = gp_client.set_GP_SETUP(msg_id, nknown, input_parameters)

    recv = gp_client.set_GP_XRD_TO_GLOBAL(msg_id, ndims, data)
    conds = []
    ##test for binaries
    #for T, tau, c in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10), np.linspace(0,1,10)):
    #    conds.append((T, tau, c))
    #test for unaries
    for T, tau in zip(np.linspace(400,1200,10), np.linspace(2.5,5,10)):
        conds.append((T, tau))
    recv = gp_client.set_GP_ALLOWED_COND(msg_id, conds)
    next_ncond = 1
    recv_cond = gp_client.get_GP_NEXT_COND(msg_id, conds, next_ncond)
    recv_cond = recv_cond[0]
    print("Next cond", recv_cond)
    print("END")


        
##    finally:
##        print ('Closing socket')
##        gp_client.close_socket()

if __name__ == "__main__":

    test_all_functions()

