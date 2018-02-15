#! /usr/bin/python3
# -*- coding: utf-8 -*-

"""Dump all variables from a C64 memory dump. Or connect to the
monitor if it is listening to a port.

Further reading:

 * Jim Butterfield, Machine Language for the Commodore 64, 128, and Other Commodore Computers, Prentice Hall Press, 1986.
 * Dan Heeb, Compute!'s VIC20 and Commodore 64 Tool Kit: BASIC, Compute!, 1984.
 * Brückmann, et al, 64 Intern, 7. erweiterte Auflage, Data Becker, 1988.
 * https://www.c64-wiki.com/wiki/Floating_point_arithmetic
"""

import struct
import argparse
import socket
import tempfile

class Variable(object):
    """Variable basis class"""
    def __init__(self, data, pos):
        """Construct a variable

        @param data: memory data
        @param pos: position of variable
        """
        self.data = data
        self.pos = pos
        self.name = chr(data[pos] & 0x7f)
        if data[pos + 1] != 0:
            self.name += chr(data[pos + 1] & 0x7f)
    def __str__(self):
        "Convert to string."
        raise NotImplementedError("string converter missing")

class IntegerVariable(Variable):
    "Integer variable, signed 16 bit."
    def __init__(self, data, pos):
        "Constructor"
        Variable.__init__(self, data, pos)
        #Yes, integer variables are stored in big endian.
        self.value = struct.unpack_from(">h", data, pos + 2)[0]
    def __str__(self):
        "Convert to string"
        return "%s%% = %d" % (self.name, self.value)


class FloatVariable(Variable):
    """Floating point variable

    See Compute!'s Toolkit p. 173.
    """
    def __init__(self, data, pos):
        "Constructor"
        Variable.__init__(self, data, pos)
        unp = struct.unpack_from("<BBBBB", data, pos + 2)
        exponent = unp[0]
        if exponent == 0:
            mantissa = 0
        else:
            mantissa = unp[1] * 2**(-32)
            mantissa += unp[2] * 2**(-24)
            mantissa += unp[3] * 2**(-16)
            mantissa += (unp[4] | 0x80) * 2**(-8)
            if unp[4] >= 128:
                mantissa *= -1
        self.value = mantissa * 2**(exponent - 128)
    def __str__(self):
        return "%s = %E" % (self.name, self.value)

class ArrayVariable(Variable):
    "Array Variables"
    def __init__(self, data, pos):
        Variable.__init__(self, data, pos)
        ivarfun = self.data[pos] >= 0x80
        ivarstr = self.data[pos + 1] >= 0x80
        if ivarfun and ivarstr:
            self.tchr = '%'
        elif ivarfun:
            raise NotImplementedError("function")
        elif ivarstr:
            self.tchr = '$'
        else:
            self.tchr = ''
        self.bytes = struct.unpack_from("<H", data, pos + 2)[0]
        self.dim = struct.unpack_from("<H", data, pos + 4)[0]
        self.nelems = [struct.unpack_from("<H", data, pos + 6 + 2*i)[0] for i in range(self.dim)]
    def __str__(self):
        nelems = ','.join("%d" % i for i in self.nelems)
        return "%s%s(%s) = %d bytes at $%04X..." % (self.name, self.tchr, nelems, self.bytes, self.pos)


class StringVariable(Variable):
    "String variable"
    def __init__(self, data, pos):
        "Constructor"
        Variable.__init__(self, data, pos)
        slen, spos = struct.unpack_from("<BH", data, pos +2)
        begin = spos
        end = spos + slen
        self.pos = (begin, end)
        self.value = self.data[spos:spos + slen]
    def __str__(self):
        "Convert to string for output."
        where = "[$%04X:$%04X]" % self.pos
        return "%s%s$ = \"%s\"" % (self.name, where, self.value)


class Dump(object):
    """Helper class to handle dumps."""
    def __init__(self, data):
        """Constructor

        @param data: binary data of dump
        """
        self.data = data
    def read_var(self, pos):
        """Read variable from memory

        @param pos: position
        """
        ivarfun = self.data[pos] >= 0x80
        ivarstr = self.data[pos + 1] >= 0x80
        if ivarfun and ivarstr:
            return IntegerVariable(self.data, pos)
        elif ivarfun:
            raise NotImplementedError("function")
        elif ivarstr:
            return StringVariable(self.data, pos)
        else:
            return FloatVariable(self.data, pos)


def parse_args():
    """Parse command-line arguments

    @return: Argument parser object
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--connect", help="connect to monitor (url)")
    parser.add_argument("file_names", help="dump file names", nargs='*')
    return parser.parse_args()


def read_socket(sock):
    """Read from socket

    Read until reading blocks.

    @return: read data
    """
    inp = sock.recv(2048)
    while True:
        #print("\"%77s\"" % inp)
        try:
            inp += sock.recv(2048, socket.MSG_DONTWAIT)
        except BlockingIOError:
            break
    return inp


def connect(url):
    """Connect to monitor

    @param url: url to connect to
    @return: file name with dump contents
    """
    #We just need a temporary name...
    tmpf = tempfile.NamedTemporaryFile(delete=False, prefix="c64.",  suffix=".dump")
    host, port = url.split(':')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((host, int(port)))
        sock.send("r\n".encode())
        read_socket(sock)
        sock.send(('bsave "%s" 0 0000 FFFF\n' % tmpf.name).encode())
        read_socket(sock)
        #We get the registers a second time in order to give the
        #process writing the data enough time. Otherwise we had
        #problems with empty files.
        sock.send("r\n".encode())
        read_socket(sock)
    return tmpf.name


def analyse_dump(fname):
    """Analyse the dump

    @param fname: file name to read dump from
    """
    print("Reading from '%s'." % fname)
    dump = Dump(open(fname, "rb").read())
    txttab, vartab, arytab, strend, fretop = struct.unpack_from("<HHHHH", dump.data, 0x2b)
    print("Beginning of BASIC program is at $%04x." %  txttab)
    print("Variables begin at $%04x." % vartab)
    print("Array variable begin at $%04x." % arytab)
    print("Top of string stack is $%04x." % fretop)
    for i in range(vartab, arytab, 7):
        #    print (i, hex(i), data[i:i+8])
        print(dump.read_var(i))
    pos = arytab
    while pos < strend:
        arr = ArrayVariable(dump.data, pos)
        print("%s" % arr)
        pos += arr.bytes


def main(argp):
    """Main function.

    @param argp: argument parser
    """
    if argp.connect is not None:
        fname = connect(argp.connect)
        analyse_dump(fname)
    else:
        for fname in argp.file_names:
            analyse_dump(fname)

if __name__ == "__main__":
    main(parse_args())