from __future__ import annotations

from http import HTTPStatus


class PlaceRoutesMixin:
    def serve_place_config(self, _user: dict) -> None:
        self.send_json(self.context.PLACE_SERVICE.public_config(), HTTPStatus.OK)

    def serve_place_search(self, query: dict[str, list[str]], _user: dict) -> None:
        if not self.allow_request("place-search", 60, 60):
            return
        search_query = str(query.get("q", [""])[0] or "")
        if len(search_query) > 80:
            self.send_json({"error": "장소 검색어는 80자 이하여야 합니다."}, HTTPStatus.BAD_REQUEST)
            return
        payload = self.context.PLACE_SERVICE.search(search_query)
        payload["items"] = self.context.STORE.enrich_place_results(payload.get("items", []))
        self.send_conditional_json(payload)

    def serve_place_verification(self, query: dict[str, list[str]], user: dict) -> None:
        room_id = str(query.get("room_id", [""])[0] or "")
        payload = self.context.STORE.place_verification_status(user["username"], room_id)
        if payload is None:
            self.send_json({"error": "장소 채팅방을 찾을 수 없습니다."}, HTTPStatus.NOT_FOUND)
            return
        self.send_json(payload, HTTPStatus.OK, headers={"Cache-Control": "no-store"})

    def open_place_room(self, user: dict) -> None:
        self.run_json_command(lambda payload: self.context.APPLICATION.open_place_room(user, payload))

    def verify_place(self, user: dict) -> None:
        if not self.allow_request("place-verify", 20, 60):
            return
        self.run_json_command(lambda payload: self.context.APPLICATION.verify_place(user, payload))
