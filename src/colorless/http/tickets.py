from __future__ import annotations


class TicketRoutesMixin:
    def serve_ticket_dashboard(self, user: dict) -> None:
        dashboard = self.context.STORE.get_ticket_dashboard(user["username"])
        if dashboard is None:
            self.send_json({"error": "사용자를 찾을 수 없습니다."}, self.context.HTTPStatus.NOT_FOUND)
            return
        self.send_json(dashboard, self.context.HTTPStatus.OK, headers={"Cache-Control": "no-store"})

    def designate_ticket_identity(self, user: dict) -> None:
        if not self.allow_request(f"ticket-identity:{user['username']}", 10, 60 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        identity, error = self.context.STORE.designate_baseball_identity(
            user["username"], str(payload.get("identityId", "")).strip()
        )
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        account_context = self.context.STORE.get_account_context(user["username"]) or {}
        self.send_json({"identity": identity, **account_context}, self.context.HTTPStatus.OK)

    def create_ticket_listing(self, user: dict) -> None:
        if not self.allow_request(f"ticket-listing:{user['username']}", 30, 60 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        try:
            purchase_price = int(payload.get("purchasePrice", 0))
            unit_price = int(payload.get("unitPrice", 0))
            quantity = int(payload.get("quantity", 0))
        except (TypeError, ValueError):
            self.send_json({"error": "가격과 수량을 올바르게 입력해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        listing, error = self.context.STORE.create_ticket_listing(
            user["username"],
            game_date=str(payload.get("gameDate", "")),
            stadium=str(payload.get("stadium", "")),
            away_team=str(payload.get("awayTeam", "")),
            seat_grade=str(payload.get("seatGrade", "")),
            seat_detail=str(payload.get("seatDetail", "")),
            purchase_price=purchase_price,
            unit_price=unit_price,
            quantity=quantity,
            delivery_method=str(payload.get("deliveryMethod", "")),
            description=str(payload.get("description", "")),
            signature_image=str(payload.get("signatureImage", "")),
        )
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"listing": listing}, self.context.HTTPStatus.CREATED)

    def delete_ticket_listing(self, user: dict) -> None:
        if not self.allow_request(f"ticket-delete:{user['username']}", 30, 60 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        listing, error = self.context.STORE.delete_ticket_listing(
            user["username"], str(payload.get("listingId", "")).strip()
        )
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"listing": listing}, self.context.HTTPStatus.OK)

    def sign_ticket_agreement(self, user: dict) -> None:
        if not self.allow_request(f"ticket-agreement:{user['username']}", 10, 60 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        access, error = self.context.STORE.sign_ticket_seller_agreement(
            user["username"], str(payload.get("signature", ""))
        )
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"ticket_access": access}, self.context.HTTPStatus.OK)

    def report_ticket_listing(self, user: dict) -> None:
        if not self.allow_request(f"ticket-report:{user['username']}", 20, 60 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        report, error = self.context.STORE.report_ticket_listing(
            user["username"],
            str(payload.get("listingId", "")).strip(),
            str(payload.get("reason", "")).strip(),
            str(payload.get("description", "")),
        )
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"report": report}, self.context.HTTPStatus.CREATED)

    def request_ticket_verification(self, user: dict) -> None:
        if not self.allow_request(f"ticket-verification:{user['username']}", 10, 60 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        access, error = self.context.STORE.request_ticket_verification(
            user["username"], str(payload.get("note", ""))
        )
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"ticket_access": access}, self.context.HTTPStatus.OK)

    def update_ticket_moderation(self, user: dict) -> None:
        if not self.allow_request(f"ticket-moderation:{user['username']}", 120, 60 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        result, error = self.context.STORE.admin_update_ticket_user(
            user["username"],
            str(payload.get("userId", "")).strip(),
            str(payload.get("action", "")).strip(),
            str(payload.get("reportId", "")).strip(),
        )
        if error:
            status = (
                self.context.HTTPStatus.FORBIDDEN
                if "@itsyou" in error
                else self.context.HTTPStatus.BAD_REQUEST
            )
            self.send_json({"error": error}, status)
            return
        self.send_json(result, self.context.HTTPStatus.OK)

    def open_ticket_admin_chat(self, user: dict) -> None:
        if not self.allow_request(f"ticket-admin-chat:{user['username']}", 20, 60 * 60):
            return
        room, created, error = self.context.STORE.open_ticket_admin_chat(user["username"])
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json(
            {"room": room, "created": created},
            self.context.HTTPStatus.CREATED if created else self.context.HTTPStatus.OK,
        )

    def open_ticket_deal(self, user: dict) -> None:
        if not self.allow_request(f"ticket-deal:{user['username']}", 60, 60 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        try:
            quantity = int(payload.get("quantity", 0))
        except (TypeError, ValueError):
            self.send_json({"error": "거래 수량을 올바르게 입력해 주세요."}, self.context.HTTPStatus.BAD_REQUEST)
            return
        deal, created, error = self.context.STORE.open_ticket_deal(
            user["username"],
            str(payload.get("listingId", "")).strip(),
            str(payload.get("buyerUserId", "")).strip(),
            quantity,
        )
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json(
            {"deal": deal},
            self.context.HTTPStatus.CREATED if created else self.context.HTTPStatus.OK,
        )

    def complete_ticket_deal(self, user: dict) -> None:
        if not self.allow_request(f"ticket-complete:{user['username']}", 60, 60 * 60):
            return
        payload = self.read_json_body()
        if payload is None:
            return
        deal, listing, error = self.context.STORE.complete_ticket_deal(
            user["username"], str(payload.get("dealRoomId", "")).strip()
        )
        if error:
            self.send_json({"error": error}, self.context.HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"deal": deal, "listing": listing}, self.context.HTTPStatus.OK)
