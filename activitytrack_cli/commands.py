# SPDX-License-Identifier: GPL-3.0-or-later
"""What each command does, on the library's rules; the text or JSON it prints."""

import getpass
import json
import sys

import activitytrack
from activitytrack import texts, updates
from activitytrack.i18n import _
from activitytrack.manifest import ManifestError
from activitytrack.pairing import PairError, PairLink
from activitytrack.texts import hm
from activitytrack import homelist, model
from activitytrack.work import PAUSE, STOP, SUSPEND, TRAVEL, WORK, StartRequest

from activitytrack.app import InputError as CliError, parse_day, parse_minute, parse_when
from .term import choose, confirm

# the exit status of "update": a newer version is there (0 nothing to do, 1 failed)
UPDATE_AVAILABLE = 10
PERIODS = {"today": -1, "24h": 24, "3d": 72, "7d": 168, "30d": 720, "all": 0}
COMMANDS = {"pause": PAUSE, "resume": WORK, "travel": TRAVEL, "stop": STOP}
WRITES = {"start", "pause", "resume", "travel", "stop", "edit", "reopen", "delete", "restore", "absence", "todo"}
ABSENCE_KINDS = {"absence": model.ABSENCE, "holiday": "HOLIDAY", "leave": "LEAVE", "sick": "SICK"}


def dispatch(app, args, out):
    command = args.command or "status"
    if command in WRITES and not app.paired:
        # the data stay local until a pairing: allowed, but said
        out.error(_("Attenzione: il PC non è associato a un server; i dati restano qui."))
    handler = HANDLERS[command]
    writes = command in WRITES and not (command == "todo" and args.todo_command in (None, "list"))
    # the JSON of a change waits for the sync, to say how it went
    args.held = [] if writes and args.json else None
    result = handler(app, args, out)
    if writes:
        # what the command left to tell (an end moved back, a planned segment removed): stderr, or "notices"
        notices = app.work.take_notices()
        if not args.json:
            for text in notices:
                out.error(text)
        state, sync_notices = _after_change(app, args, out)
        for data in args.held or []:
            _print_json({**data, "sync": state, "notices": notices + sync_notices} if isinstance(data, dict) else data)
    return result or 0


def _print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=1))


def _emit(args, out, data, text):
    if not args.json:
        text()
    elif getattr(args, "held", None) is not None:
        args.held.append(data)
    else:
        _print_json(data)


def _after_change(app, args, out):
    """
    The change leaves at once; without a network at the next sync. The
    state of the sync, None when not tried (--no-sync); in text a failure
    is said on stderr, in JSON it is the "sync" of the answer. Returns
    (state, the notices of the sync): said once, in text on stderr (a
    failure's line already has them), in JSON as part of "notices", then
    dismissed.
    """
    if not app.paired:
        return "unpaired", []
    if args.no_sync:
        return None, []
    state, detail = app.sync(full=True)
    failed = state not in ("ok", "idle")
    if failed and not args.json:
        out.error(texts.sync_state(app.session, state, detail))
    notices = texts.sync_notices(app.session)
    if notices and not failed and not args.json:
        for text in notices:
            out.error(text)
    app.session.store.dismiss_notices()
    return state, notices


# ---- status ----

def show_entry(app, out, info):
    since = info["since"][11:16] if info["since"] else ""
    parts = [out.state(info["display"], info["state"]) + (" " + _("dalle %s") % since if since else "")]
    if info["kind"] == model.ABSENCE:
        parts.append(f"{texts.KINDS[model.ABSENCE]} {hm(info['absence_min'] * 60_000)}")
    else:
        parts.append(f"{texts.KINDS[WORK]} {hm(info['work_min'] * 60_000)}")
    if info["travel_min"]:
        parts.append(f"{texts.KINDS[TRAVEL]} {hm(info['travel_min'] * 60_000)}")
    if info["priority"]:
        parts.append(info["priority_label"])
    if info["needs_review"]:
        parts.append(out.paint(_("da verificare"), "red"))
    out.line(f"  {out.paint(info['id'], 'cyan')}  {out.paint(info['label'], 'bold')}")
    out.line(f"          {' · '.join(parts)}")


def status(app, args, out):
    running = [app.entry_info(e) for e in app.work.running()]
    sync = app.sync_info()
    # what the last sync brought: no network here
    known = app.updates.status()
    news = texts.update_state(known, "") if known.state == updates.AVAILABLE else ""

    def text():
        if running:
            out.heading(_("In corso"))
            for info in running:
                show_entry(app, out, info)
        else:
            out.line(_("Nessuna attività in corso"))
        out.line()
        out.line(out.paint(sync["text"], "red" if sync["attention"] else "grey"))
        if news:
            out.line(out.paint(news + " " + _("(activitytrack update)"), "yellow"))
    _emit(args, out, {"running": running, "sync": sync}, text)


# ---- activities ----

def _tags(values):
    out = []
    for v in values or []:
        out += [t.strip() for t in v.split(",") if t.strip()]
    return out


def start(app, args, out):
    todo = app.find("entry", args.todo)["uid"] if args.todo else None
    req = StartRequest(activity=" ".join(args.description), client=args.client, company=args.company,
                       role=args.role, tags=_tags(args.tag), mode=TRAVEL if args.travel else WORK, todo=todo,
                       start_ms=parse_when(args.at) if args.at else None,
                       end_ms=parse_when(args.until) if args.until else None)
    if args.until is None and args.others != "keep":
        # a paused activity is suspended as well (§3)
        req.others = {e["uid"]: SUSPEND if args.others == "suspend" else STOP for e in app.work.running()
                      if app.work.open_segment(e["uid"]) is not None}
    uid = app.work.start(req)
    info = app.entry_info(app.work.entry(uid))
    _emit(args, out, info, lambda: (out.line(_("Avviata:")), show_entry(app, out, info)))


def _interactive(args):
    """Questions are asked only in text mode on a terminal; a script gets the plain answer."""
    return not args.json and sys.stdin.isatty()


def _at(args):
    return parse_when(args.at) if args.at else None


def command(app, args, out):
    """pause, resume, travel, stop: the named commands of the library by the state of the activity."""
    work = app.work
    cmd = args.command
    try:
        e = app.running_one(args.id, active_only=cmd not in ("resume", "travel", "stop"))
    except CliError:
        if cmd != "pause":
            raise
        # nothing is being worked: pause falls back to the only one running, suspended (the tui's "p")
        e = app.running_one(args.id)
    uid, at, exact = e["uid"], _at(args), bool(args.at)
    seg = work.open_segment(uid)
    if cmd == "pause" and seg is None:
        # a suspended activity has nothing to pause: it goes back to work, as "p" in the tui
        work.resume(uid, WORK, at, exact)
    elif cmd == "pause":
        work.switch(uid, PAUSE, at, exact)
    elif cmd == "resume":
        # from a pause the kind before it; suspended, work (or the kind open elsewhere: nothing to do)
        if seg is None:
            work.resume(uid, WORK, at, exact)
        else:
            work.switch(uid, work.resume_kind(uid) if seg["kind"] == PAUSE else WORK, at, exact)
    elif cmd == "travel":
        if work.asks_completion(uid):
            completed = args.completed
            if completed is None:
                completed = (_interactive(args) and
                             confirm(out, _("Hai completato l'attività lavorativa?"), False))
            uid = work.travel_from_work(uid, completed, at)
        elif seg is None:
            work.resume(uid, TRAVEL, at, exact)
        else:
            work.switch(uid, TRAVEL, at, exact)
    else:
        _stop(app, args, out, uid, at, exact)
    info = app.entry_info(work.entry(uid))
    _emit(args, out, info, lambda: show_entry(app, out, info))


def _stop(app, args, out, uid, at, exact):
    """Stop; when the return trip is offered (§3.2) the three choices, by flag or asked."""
    work = app.work
    choice = args.choice
    if choice is None and work.return_trip_offered(uid) and _interactive(args):
        options = [_("Avvia viaggio di ritorno"), _("Termina l'attività")]
        if work.return_trip_addable(uid):
            options.append(_("Termina l'attività ed aggiungi il viaggio di ritorno"))
        i = choose(out, _("Fermi l'attività"), options)
        if i is None:
            raise CliError(_("Annullato."))
        choice = ("return", "end", "both")[i]
    if choice == "return":
        work.travel_back(uid, at)
    else:
        work.stop(uid, at, exact, return_trip=choice == "both")


def reopen(app, args, out):
    e = app.find("entry", args.id)
    if not args.yes and _interactive(args):
        label = " ".join(app.label(e).split())
        if not confirm(out, _("Riaprire «%s»?") % label):
            return 0
    app.work.reopen(e["uid"], TRAVEL if args.travel else WORK, _at(args), bool(args.at))
    info = app.entry_info(app.work.entry(e["uid"]))
    _emit(args, out, info, lambda: show_entry(app, out, info))


def absence(app, args, out):
    first = parse_day(args.first)
    last = parse_day(args.last) if args.last else None
    start_min = parse_minute(args.from_time) if args.from_time else None
    end_min = parse_minute(args.to_time) if args.to_time else None
    company = app.find("company", args.company)["uid"] if args.company else None
    uid = app.work.save_absence(None, first, last, company=company, absence_kind=ABSENCE_KINDS[args.kind],
                                notes=" ".join(args.notes), start_min=start_min, end_min=end_min)
    info = app.entry_info(app.work.entry(uid))
    _emit(args, out, info, lambda: (out.line(_("Assenza registrata:")), show_entry(app, out, info)))


def edit(app, args, out):
    e = app.find("entry", args.id)
    app.work.edit(e["uid"], activity=args.description, client=args.client, company=args.company, role=args.role,
                  locality=args.locality, notes=args.notes, tags=_tags(args.tag) if args.tag is not None else None)
    info = app.entry_info(app.work.entry(e["uid"]))
    _emit(args, out, info, lambda: show_entry(app, out, info))


def delete(app, args, out):
    e = app.find("entry", args.id)
    if not args.yes and not args.json:
        label = " ".join(app.work.label(e).split())
        if not confirm(out, _("Eliminare «%s»? Si elimina anche sugli altri dispositivi.") % label):
            return 0
    app.work.delete(e["uid"])
    _emit(args, out, {"deleted": e["uid"]}, lambda: out.line(_("Eliminata.")))


def restore(app, args, out):
    e = app.find("entry", args.id)
    app.work.restore(e["uid"])
    info = app.entry_info(app.work.entry(e["uid"]))
    _emit(args, out, info, lambda: (out.line(_("Ripristinata:")), show_entry(app, out, info)))


# ---- the Home list and the to-dos ----

def home(app, args, out, todos_only=False):
    from .main import SHOW
    show = [s.strip().lower() for s in args.show.split(",") if s.strip()]
    for s in show:
        if s not in SHOW:
            raise CliError(_("Stato sconosciuto in --show: «%s» (validi: %s).") % (s, ", ".join(SHOW)))
    show = [s.upper() for s in show]
    company = app.find("company", args.company)["uid"] if args.company else None
    groups = homelist.home(app.work, show, PERIODS[args.period], company, args.tag)
    data = []
    for section, items in groups:
        rows = []
        for kind, obj, extra in items:
            if kind == "todo":
                rows.append({"kind": "todo", **app.entry_info(obj)})
            elif not todos_only:
                rows.append({"kind": "entry", **app.entry_info(obj), "end": extra.isoformat(timespec="minutes")})
        if rows:
            data.append({"section": section, "title": texts.SECTIONS[section], "items": rows})

    def text():
        if not data:
            out.line(_("Niente in questo intervallo."))
        for group in data:
            out.heading(group["title"])
            for r in group["items"]:
                title = r["activity"] if r["kind"] == "todo" else r["label"]
                right = r["when"] if r["kind"] == "todo" else r["end"][11:16]
                who = " · ".join(x for x in (r["client"] or r["company"] if r["kind"] == "todo" else "",
                                             r["priority_label"] if r["priority"] else "") if x)
                out.line(f"  {out.paint(r['id'], 'cyan')}  {out.paint(title, 'bold')}  {out.paint(right, 'grey')}")
                out.line(f"          {out.state(r['display'], r['state'])}" + (f" · {who}" if who else ""))
    _emit(args, out, data, text)


def list_(app, args, out):
    home(app, args, out)


def _todo_kwargs(app, args, existing=None):
    t = existing or {}
    kw = dict(company=args.company if args.company is not None else t.get("companyUid"),
              client=args.client if args.client is not None else t.get("clientUid"),
              role=t.get("roleUid"), locality=t.get("locality") or "", tags=t.get("tags") or (),
              notes=args.notes if args.notes is not None else (t.get("notes") or ""),
              refs=("\n".join(r.strip() for r in args.refs.split(",")) if args.refs is not None else None),
              priority=args.priority if args.priority is not None else (t.get("priority") or 0))
    if args.due is not None:
        parts = args.due.split() or [""]
        kw["due_date"] = parse_day(parts[0])
        kw["due_min"] = parse_minute(parts[1]) if len(parts) > 1 else None
    elif t.get("dueDate"):
        kw["due_date"], kw["due_min"] = parse_day(t["dueDate"]), t.get("dueTimeMin")
    if args.plan is not None:
        parts = args.plan.split() or [""]
        kw["plan_date"] = parse_day(parts[0])
        kw["plan_min"] = parse_minute(parts[1]) if len(parts) > 1 else None
        kw["duration_min"] = _minutes(parts[2]) if len(parts) > 2 else None
    elif t.get("planDate"):
        kw["plan_date"] = parse_day(t["planDate"])
        kw["plan_min"], kw["duration_min"] = t.get("planStartMin"), t.get("durationMin")
    return kw


def _minutes(text):
    if not (text.isascii() and text.isdecimal()):
        raise CliError(_("Durata non valida: «%s» (in minuti, per esempio 45).") % text)
    return int(text)


def todo(app, args, out):
    sub = args.todo_command or "list"
    work = app.work
    if sub == "list":
        # "activitytrack todo" alone: every open to-do
        return home(app, args if hasattr(args, "period") else _defaults(args), out, todos_only=True)
    if sub == "add":
        uid = work.save_todo(None, " ".join(args.title), **_todo_kwargs(app, args))
    elif sub == "edit":
        t = app.find("entry", args.id)
        uid = work.save_todo(t["uid"], args.title or t.get("activity"), **_todo_kwargs(app, args, t))
    else:
        t = app.find("entry", args.id)
        uid = t["uid"]
        if sub == "start":
            args.description, args.client, args.company, args.role, args.tag = [], None, None, None, []
            args.travel, args.at, args.until, args.others, args.todo = False, None, None, "suspend", uid
            return start(app, args, out)
        if sub == "done":
            work.tick(uid)
        elif sub in ("ignore", "delete"):
            work.delete(uid)
        elif sub == "reopen":
            if t.get("deletedUtcMs") is not None:
                work.restore(uid)
            else:
                work.untick(uid)
        elif sub == "postpone":
            if work.postpone(uid, args.minutes) is None:
                raise CliError(_("Il Da fare non è pianificato: niente da posticipare."))
    info = app.entry_info(work.entry(uid))

    def text():
        who = info["client"] or info["company"]
        out.line(f"  {out.paint(info['id'], 'cyan')}  {out.paint(info['activity'], 'bold')}  "
                 f"{out.paint(info['when'], 'grey')}")
        out.line(f"          {out.state(info['display'], info['state'])}" + (f" · {who}" if who else ""))
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


def update(app, args, out):
    """The paired server's announcement of a newer version (activitytrack#1): asked now, its signature
    checked against the key of the pairing; --download fetches the package, its sha256 checked."""
    version = activitytrack.client()["version"]
    status = app.updates.check(channels=args.channel)
    text = texts.update_state(status, version)
    if status.state in updates.FAILED:
        raise CliError(text)
    release = status.release
    data = {"installed": version, "state": status.state, "available": release is not None,
            "version": release.version if release else None, "channel": release.channel if release else None,
            "downloadable": status.downloadable, "text": text}
    if args.download and release is not None:
        if not status.downloadable:
            raise CliError(text)
        try:
            data["downloaded"] = str(app.updates.download(release))
        except ManifestError as e:
            raise CliError(_("Scaricamento non riuscito: %s") % e) from None
    _emit(args, out, data, lambda: (
        out.line(out.paint(text, "yellow" if release else "grey")),
        data.get("downloaded") and out.line(_("Scaricato e controllato: %s") % data["downloaded"])))
    return UPDATE_AVAILABLE if release is not None and not args.download else 0


def _pair_link(args):
    """
    The link: from the prompt, not shown (the code would stay in the shell's
    history and, while it runs, in the processes list); from the standard
    input with "-" or when it is not a terminal; as the argument, as before.
    """
    if args.link is not None and args.link != "-":
        return args.link
    if args.link is None and sys.stdin.isatty():
        try:
            return getpass.getpass(_("Collegamento della pagina «Dispositivi» del web (non viene mostrato): "))
        except EOFError:
            return ""
    return sys.stdin.readline()


def pair(app, args, out):
    try:
        link = PairLink.parse(_pair_link(args), allow_http=args.dev)
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
    "status": status, "start": start, "edit": edit, "reopen": reopen, "delete": delete, "restore": restore,
    "absence": absence, "list": list_, "todo": todo, "sync": sync, "update": update, "pair": pair, "unpair": unpair,
    **{name: command for name in COMMANDS},
}
