import socket
import selectors
import ClientHandler

class SMTPServer():
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.CH = ClientHandler.ClientHandler()
        self.CH.start()

    def setup(self):
        lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        lsock.bind((host, port))
        lsock.listen()
        print("listening on", (host, port))
        lsock.setblocking(False)
        events = selectors.EVENT_READ
        self.CH.register(lsock, events, data=None)

    def run(self):
        self.setup()
        try:
            while True:
                userInput = input("Enter a command: ")
                print(userInput)
                self.CH.enqueue(userInput)
        except KeyboardInterrupt:
            print("caught keyboard interrupt, exiting")

if __name__ == "__main__":
    host = "127.0.0.1"#input("Please Enter a host address: ")
    port = 50003 #int(input("Please enter a port number: "))
    server = SMTPServer(host,port)
    server.run()