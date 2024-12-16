import io
import sys
import json
import types
import struct
import selectors
from threading import Thread


class ClientHandler(Thread):
    def __init__(self):
        Thread.__init__(self)

        self.sel = selectors.DefaultSelector()
        self.clients = []

    def register(self, conn, events, data):
        self.sel.register(conn, events, data=data)
        self.clients.append(conn)

    def accept_wrapper(self, sock):
        conn, addr = sock.accept()  # Should be ready to read
        print("accepted connection from", addr, conn)
        conn.setblocking(False)

        data = types.SimpleNamespace(addr=addr, inb=b"", outb=b"", jsonHeaderLen=None, jsonHeader=None, request=None, response_created = False)

        events = selectors.EVENT_READ | selectors.EVENT_WRITE
        self.register(conn, events, data=data)

    def service_connection(self, key, mask):
        if mask & selectors.EVENT_READ:
            self.read(key)
        else:
            print("closing connection to", key.data.addr)
            self.sel.unregister(key.fileobj)
            key.fileobj.close()
        if mask & selectors.EVENT_WRITE:
            if key.data.outb:
                self.write(key)

    def run(self):
        try:
            while True:
                try:
                    events = self.sel.select(timeout=None)
                    for key, mask in events:
                        if key.data is None:
                            self.accept_wrapper(key.fileobj)
                        else:
                            self.service_connection(key, mask)
                except OSError as e:
                    pass
        except KeyboardInterrupt:
            print("caught keyboard interrupt, exiting")

        finally:
            self.sel.close()

    def _read(self, key):
        try:
            # Should be ready to read
            tempData = key.fileobj.recv(4096)
        except BlockingIOError:
            # Resource temporarily unavailable (errno EWOULDBLOCK)
            pass
        else:
            if tempData:
                key.data.inb += tempData
            else:
                raise RuntimeError("Peer closed.")

    def _write(self, key):
        if key.data.outb:
            print("sending", repr(key.data.outb), "to", key.fileobj.getpeername())
            try:
                # Should be ready to write
                sent = key.fileobj.send(key.data.outb)
            except BlockingIOError:
                # Resource temporarily unavailable (errno EWOULDBLOCK)
                pass
            else:
                key.data.outb = key.data.outb[sent:]
                # Close when the buffer is drained. The response has been sent.

    def _json_encode(self, obj, encoding):
        return json.dumps(obj, ensure_ascii=False).encode(encoding)

    def _json_decode(self, json_bytes, encoding):
        tiow = io.TextIOWrapper(
            io.BytesIO(json_bytes), encoding=encoding, newline=""
        )
        obj = json.load(tiow)
        tiow.close()
        return obj

    def _create_message(
            self, *, content_bytes, content_type, content_encoding
    ):
        jsonheader = {
            "byteorder": sys.byteorder,
            "content-type": content_type,
            "content-encoding": content_encoding,
            "content-length": len(content_bytes),
        }
        jsonheader_bytes = self._json_encode(jsonheader, "utf-8")
        message_hdr = struct.pack(">H", len(jsonheader_bytes))
        message = message_hdr + jsonheader_bytes + content_bytes
        return message

    def _create_response_json_content(self, key):
        action = key.data.request.get("action")
        if action == "search":
            query = key.data.request.get("value")
            answer = "Some kind of search ok?" #"request_search.get(query) or f'No match for "{query}".'"
            content = {"result": answer}
        else:
            content = {"result": f'Error: invalid action "{action}".'}
        content_encoding = "utf-8"
        response = {
            "content_bytes": self._json_encode(content, content_encoding),
            "content_type": "text/json",
            "content_encoding": content_encoding,
        }
        return response

    def _create_response_binary_content(self):
        response = {
            "content_bytes": b"First 10 bytes of request: "
                             + self.request[:10],
            "content_type": "binary/custom-server-binary-type",
            "content_encoding": "binary",
        }
        return response

    def read(self, key):
        self._read(key)

        if key.data.jsonHeaderLen is None:
            self.process_protoheader(key)

        if key.data.jsonHeaderLen is not None:
            if key.data.jsonHeader is None:
                self.process_jsonheader(key)

        if key.data.jsonHeader:
            if key.data.request is None:
                self.process_request(key)

    def write(self, key):
        if key.data.request:
            if not key.data.response_created:
                self.create_response(key)

        self._write(key)
        key.data.response_created = False

    def close(self):
        for connection in self.clients:
            try:
                self.sel.unregister(connection)
            except Exception as e:
                print("error: selector.unregister() exception")

            try:
                connection.close()
            except OSError as e:
                print("error: socket.close() exception for")
            finally:
                # Delete reference to socket object for garbage collection
                self.clients.remove(connection)

    def process_protoheader(self, key):
        hdrlen = 2
        if len(key.data.inb) >= hdrlen:
            key.data.jsonHeaderLen = struct.unpack(
                ">H", key.data.inb[:hdrlen]
            )[0]
            key.data.inb = key.data.inb[hdrlen:]

    def process_jsonheader(self, key):
        hdrlen = key.data.jsonHeaderLen
        if len(key.data.inb) >= hdrlen:
            key.data.jsonHeader = self._json_decode(
                key.data.inb[:hdrlen], "utf-8"
            )
            key.data.inb = key.data.inb[hdrlen:]
            for reqhdr in (
                    "byteorder",
                    "content-length",
                    "content-type",
                    "content-encoding",
            ):
                if reqhdr not in key.data.jsonHeader:
                    raise ValueError(f'Missing required header "{reqhdr}".')

    def process_request(self, key):
        content_len = key.data.jsonHeader["content-length"]
        if not len(key.data.inb) >= content_len:
            return
        data = key.data.inb[:content_len]
        key.data.inb = key.data.inb[content_len:]
        if key.data.jsonHeader["content-type"] == "text/json":
            encoding = key.data.jsonHeader["content-encoding"]
            key.data.request = self._json_decode(data, encoding)
            print("received request", repr(key.data.request), "from", key.fileobj.getpeername())
            self.create_response(key)
        else:
            # Binary or unknown content-type
            key.data.request = data
            print(f'received {key.data.jsonHeader["content-type"]} request from',key.fileobj.getpeername())

    def create_response(self, key):
        if key.data.jsonHeader["content-type"] == "text/json":
            response = self._create_response_json_content(key)
        else:
            # Binary or unknown content-type
            response = self._create_response_binary_content(key)
        message = self._create_message(**response)
        key.data.response_created = True
        key.data.outb += message