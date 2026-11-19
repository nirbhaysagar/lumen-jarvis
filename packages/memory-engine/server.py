        else:
            send(self, {"error": "unknown route"}, 404)


if __name__ == "__main__":
    print("Lumen MVP on http://localhost:8765  (DB: lumen.db, multi-threaded)")
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    server.serve_forever()

