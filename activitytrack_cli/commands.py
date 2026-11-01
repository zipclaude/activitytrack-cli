# SPDX-License-Identifier: GPL-3.0-or-later
"""What each command does, on the library's rules; the text or JSON it prints."""

import json

from activitytrack import texts
from activitytrack.i18n import _
from activitytrack.pairing import PairError, PairLink
from activitytrack.texts import hm
from activitytrack.work import PAUSE, STOP, SUSPEND, TO_COMPLETE, TRAVEL, WORK, StartRequest

from .app import CliError, parse_day, parse_minute, parse_when
from .term import MODE_COLOURS

PERIODS = {"today": -1, "24h": 24, "3d": 72, "7d": 168, "30d": 720, "all": 0}
COMMANDS = {"pause": PAUSE, "resume": WORK, "travel": TRAVEL, "suspend": SUSPEND, "stop": STOP}
WRITES = {"start", "pause", "resume", "travel", "suspend", "stop", "complete", "edit", "reopen", "delete", "todo"}


def dispatch(app, args, out):
    command = args.command or "status"
    if command in ("start", "complete", "edit", "reopen", "delete", "todo", *COMMANDS) and not app.paired:
        # the data stay local until a pairing: allowed, but said
        out.error(_("Attenzione: il PC non è associato a un server; i dati restano qui."))
    handler = HANDLERS[command]
    result = handler(app, args, out)
    if command in WRITES and not (command == "todo" and args.todo_command in (None, "list")):
        _after_change(app, args, out)
    return result or 0


def _emit(args, out, data, text):
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        text()


def _after_change(app, args, out):
    """The change leaves at once; without a network at the next sync."""
    if args.no_sync or not app.paired:
        return
    state, detail = app.sync(full=True)
    if state not in ("ok", "idle") and not args.json:
        out.error(texts.sync_state(app.session, state, detail))


# ---- status ----

def show_entry(app, out, info):
    colour = MODE_COLOURS.get(info["mode"] if info["status"] == "ACTIVE" else info["status"], "grey")
    since = info["since"][11:16] if info["since"] else ""
    parts = [out.paint(info["state"], "bold", colour) + (" " + _("dalle %s") % since if since else "")]
    parts.append(f"{texts.KINDS[WORK]} {hm(info['work_min'] * 60_000)}")
    if info["travel_min"]:
        parts.append(f"{texts.KINDS[TRAVEL]} {hm(info['travel_min'] * 60_000)}")
    if info["needs_review"]:
        parts.append(out.paint(_("da verificare"), "red"))
    out.line(f"  {out.paint(info['id'], 'cyan')}  {out.paint(info['label'], 'bold')}")
    out.line(f"          {' · '.join(parts)}")


def status(app, args, out):
    running = [app.entry_info(e) for e in app.work.running()]
    sync = app.sync_info()

    def text():
        if running:
            out.heading(_("In corso"))
            for info in running:
                show_entry(app, out, info)
        else:
            out.line(_("Nessuna attività in corso"))
        out.line()
        out.line(out.paint(sync["text"], "red" if sync["attention"] else "grey"))
    _emit(args, out, {"running": running, "sync": sync}, text)


# ---- activities ----

def _tags(values):
    out = []
    for v in values or []:
        out += [t.strip() for t in v.split(",") if t.strip()]
    return out


def start(app, args, out):
    todo = app.find("todo", args.todo)["uid"] if args.todo else None
    req = StartRequest(activity=" ".join(args.description), client=args.client, company=args.company,
                       role=args.role, tags=_tags(args.tag), mode=TRAVEL if args.travel else WORK, todo=todo,
                       start_ms=parse_when(args.at) if args.at else None,
                       end_ms=parse_when(args.until) if args.until else None)
    if todo and not args.description:
        req.activity = app.store.get("todo", todo).get("title") or ""
    if todo and not args.client:
        t = app.store.get("todo", todo)
        req.client, req.company = t.get("clientUid"), t.get("companyUid")
    if args.until is None and args.others != "keep":
        cmd = SUSPEND if args.others == "suspend" else STOP
        req.others = {e["uid"]: (STOP if e.get("mode") == PAUSE else cmd) for e in app.work.running()
                      if e.get("status") == "ACTIVE"}
    uid = app.work.start(req)
    info = app.entry_info(app.work.entry(uid))
    _emit(args, out, info, lambda: (out.line(_("Avviata:")), show_entry(app, out, info)))


def command(app, args, out):
    cmd = COMMANDS[args.command]
    e = app.running_one(args.id, active_only=cmd != WORK)
    status_ = app.work.command(e["uid"], cmd, at=parse_when(args.at) if args.at else None,
                               exact=bool(args.at))
    info = app.entry_info(app.work.entry(e["uid"]))

    def text():
        show_entry(app, out, info)
        if status_ == TO_COMPLETE:
            out.line(out.paint(_("Mancano dei dettagli: completa con «activitytrack complete %s -d …».")
                               % info["id"], "yellow"))
    _emit(args, out, info, text)


def complete(app, args, out):
    e = app.find("entry", args.id)
    if args.description is not None or args.client is not None:
        app.work.edit(e["uid"], activity=args.description, client=args.client)
    if args.todo_done and e.get("todoUid"):
        app.todos.set_done(e["todoUid"])
    app.work.complete(e["uid"], allow_empty=args.empty)
    info = app.entry_info(app.work.entry(e["uid"]))
    _emit(args, out, info, lambda: (out.line(_("Completata:")), show_entry(app, out, info)))


def edit(app, args, out):
    e = app.find("entry", args.id)
    app.work.edit(e["uid"], activity=args.description, client=args.client, company=args.company, role=args.role,
                  locality=args.locality, notes=args.notes, tags=_tags(args.tag) if args.tag is not None else None)
    info = app.entry_info(app.work.entry(e["uid"]))
    _emit(args, out, info, lambda: show_entry(app, out, info))


def reopen(app, args, out):
    e = app.find("entry", args.id)
    app.work.command(e["uid"], "REOPEN")
    info = app.entry_info(app.work.entry(e["uid"]))
    _emit(args, out, info, lambda: show_entry(app, out, info))


def delete(app, args, out):
    e = app.find("entry", args.id)
    if not args.yes and not args.json:
        from .term import confirm
        label = " ".join(app.work.label(e).split())
        if not confirm(out, _("Eliminare «%s»? Si elimina anche sugli altri dispositivi.") % label):
            return 0
    app.work.delete(e["uid"])
    _emit(args, out, {"deleted": e["uid"]}, lambda: out.line(_("Eliminata.")))


# ---- the Home list and the to-dos ----

def home(app, args, out, todos_only=False):
    show = [s.strip().upper() for s in args.show.split(",") if s.strip()]
    company = app.find("company", args.company)["uid"] if args.company else None
    groups = app.todos.home(show, PERIODS[args.period], company, args.tag)
    data = []
    for section, items in groups:
        rows = []
        for kind, obj, extra in items:
            if kind == "todo":
                rows.append({"kind": "todo", **app.todo_info(obj)})
            elif not todos_only:
                info = app.entry_info(obj)
                rows.append({"kind": "entry", **info, "end": extra.isoformat(timespec="minutes")})
        if rows:
            data.append({"section": section, "title": texts.SECTIONS[section], "items": rows})

    def text():
        if not data:
            out.line(_("Niente in questo intervallo."))
        for group in data:
            out.heading(group["title"])
            for r in group["items"]:
                if r["kind"] == "todo":
                    colour = "red" if r["state"] == "overdue" else "dim" if r["state"] in ("done", "ignored") else None
                    who = " · ".join(x for x in (r["client"] or r["company"], r["state_label"],
                                                 f"P{r['priority']}" if r["priority"] else "") if x)
                    title = out.paint(r["title"], *(["bold"] + ([colour] if colour else [])))
                    out.line(f"  {out.paint(r['id'], 'cyan')}  {title}  {out.paint(r['when'], 'grey')}")
                    out.line(f"          {out.paint(who, colour or 'grey')}")
                else:
                    out.line(f"  {out.paint(r['id'], 'cyan')}  {r['label']}  {out.paint(r['end'][11:16], 'grey')}")
                    out.line(f"          {out.paint(r['state'], 'red' if r['status'] == TO_COMPLETE else 'grey')}")
    _emit(args, out, data, text)


def list_(app, args, out):
    home(app, args, out)


def _todo_kwargs(app, args, existing=None):
    t = existing or {}
    kw = dict(company=args.company if args.company is not None else t.get("companyUid"),
              client=args.client if args.client is not None else t.get("clientUid"),
              notes=args.notes if args.notes is not None else (t.get("notes") or ""),
              refs=("\n".join(r.strip() for r in args.refs.split(",")) if args.refs is not None
                    else (t.get("refs") or "")),
              priority=args.priority if args.priority is not None else t.get("priority"))
    if args.due is not None:
        parts = args.due.split()
        kw["due_date"] = parse_day(parts[0])
        kw["due_min"] = parse_minute(parts[1]) if len(parts) > 1 else None
    elif t.get("dueDate"):
        kw["due_date"], kw["due_min"] = parse_day(t["dueDate"]), t.get("dueTimeMin")
    if args.plan is not None:
        parts = args.plan.split()
        kw["plan_date"] = parse_day(parts[0])
        kw["plan_min"] = parse_minute(parts[1]) if len(parts) > 1 else None
        kw["duration_min"] = int(parts[2]) if len(parts) > 2 else None
    elif t.get("planDate"):
        kw["plan_date"] = parse_day(t["planDate"])
        kw["plan_min"], kw["duration_min"] = t.get("planStartMin"), t.get("durationMin")
    return kw


def todo(app, args, out):
    sub = args.todo_command or "list"
    if sub == "list":
        # "activitytrack todo" alone: every open to-do
        return home(app, args if hasattr(args, "period") else _defaults(args), out, todos_only=True)
    if sub == "add":
        uid = app.todos.save(None, " ".join(args.title), **_todo_kwargs(app, args))
    elif sub == "edit":
        t = app.find("todo", args.id)
        uid = app.todos.save(t["uid"], args.title or t.get("title"), **_todo_kwargs(app, args, t))
    else:
        t = app.find("todo", args.id)
        uid = t["uid"]
        if sub == "start":
            args.description, args.client, args.company, args.role, args.tag = [], None, None, None, []
            args.travel, args.at, args.until, args.others, args.todo = False, None, None, "suspend", uid
            return start(app, args, out)
        if sub == "done":
            app.todos.set_done(uid)
        elif sub == "ignore":
            app.todos.set_ignored(uid)
        elif sub == "reopen":
            app.todos.reopen(uid)
        elif sub == "postpone":
            if app.todos.postpone(uid, args.minutes) is None:
                raise CliError(_("Il Da fare non è pianificato: niente da posticipare."))
        elif sub == "delete":
            app.todos.delete(uid)
            _emit(args, out, {"deleted": uid}, lambda: out.line(_("Eliminato.")))
            return 0
    info = app.todo_info(app.todos.todo(uid))

    def text():
        who = info["client"] or info["company"]
        out.line(f"  {out.paint(info['id'], 'cyan')}  {out.paint(info['title'], 'bold')}  "
                 f"{out.paint(info['when'], 'grey')}")
        out.line(f"          {' · '.join(x for x in (who, info['state_label']) if x)}")
    _emit(args, out, info, text)


def _defaults(args):
    args.period, args.show, args.company, args.tag = "all", "todo", None, None
    return args


# ---- sync and pairing ----

def sync(app, args, out):
    if not app.paired:
        raise CliError(texts.sync_state(app.session))
    state, detail = app.sync(full=True)
    info = app.sync_info(state, detail)
    _emit(args, out, info, lambda: out.line(out.paint(info["text"], "red" if info["attention"] else "green")))
    return 0 if state in ("ok", "idle") else 1


def pair(app, args, out):
    try:
        link = PairLink.parse(args.link, allow_http=args.dev)
        account = app.session.pair(link)
    except PairError as e:
        raise CliError(texts.pair_error(e.code)) from None
    state, detail = app.sync(full=True)
    data = {"server": account.server, "user": account.username, "org": account.org, "sync": state}
    _emit(args, out, data, lambda: (
        out.line(out.paint(_("Associato a %s (%s, %s).") % (account.server, account.username, account.org),
                           "green")),
        out.line(texts.sync_state(app.session, state, detail))))


def unpair(app, args, out):
    app.session.unpair()
    _emit(args, out, {"unpaired": True},
          lambda: out.line(_("Dissociato: i dati restano qui. Per sincronizzare di nuovo associa il PC dal web.")))


HANDLERS = {
    "status": status, "start": start, "complete": complete, "edit": edit, "reopen": reopen, "delete": delete,
    "list": list_, "todo": todo, "sync": sync, "pair": pair, "unpair": unpair,
    **{name: command for name in COMMANDS},
}
