from ..threads import Threads, ThreadError, READ_ACTIONS, WRITE_ACTIONS


class ThreadRoutesMixin:
    def serve_threads(self, action, query=None):
        user = self.require_auth()
        if user is None:
            return
        write = query is None
        if action not in (WRITE_ACTIONS if write else READ_ACTIONS):
            self.send_json({"error": "지원하지 않는 작업입니다."}, 404)
            return
        payload = self.read_json_body() if write else {key: values[0] for key, values in query.items()}
        if payload is None:
            return
        if write and not self.allow_actor_request(user, "threads-" + action, 30 if action == "create" else 60, 60):
            return
        try:
            service = Threads(self.context.STORE, user)
            result = service.write(action, payload) if write else service.read(action, payload)
            self.send_json(result, 200, headers={"Cache-Control": "no-store"})
        except ThreadError as error:
            self.send_json({"error": error.message}, error.status)

