# activitytrack-cli

[ActivityTrack](https://gitlab.com/activitytrack/activitytrack) from the command line: start,
pause and stop activities, record absences, list and work the to-dos, sync with
[ActivityTrack Web](https://gitlab.com/activitytrack/activitytrack-web).

It is the same application as [ActivityTrack Desktop](https://gitlab.com/activitytrack/activitytrack-desktop)
and the text interface [activitytrack-tui](https://gitlab.com/activitytrack/activitytrack-tui): they
share the data folder, the pairing and the token, through the library
[python3-activitytrack](https://gitlab.com/activitytrack/python3-activitytrack). What the command line
does shows at once in the tray icon.

```sh
activitytrack pair                                     # paste the link of the web's Dispositivi page
activitytrack start Configurazione VPN -c "Comune di Valdora" -t urgente
activitytrack pause; activitytrack resume
activitytrack travel                                   # asks if the work is complete
activitytrack stop                                     # offers the return trip when it applies
activitytrack absence domani --kind holiday            # Assenza, Ferie, Permesso, Malattia
activitytrack list                                     # the Home: to-dos and stopped activities
activitytrack todo add Rinnovo certificati -c Hotel --plan "domani 9:30 45"
activitytrack todo start b84
activitytrack -i                                       # interactive, numbered lists in colour
activitytrack --json status                            # for scripts
```

`pair` asks for the link without showing it: given as an argument, it would stay in the
shell's history (`pair -` reads it from the standard input, for scripts).

`man activitytrack` has every command.

## Install

From the [releases](https://gitlab.com/activitytrack/activitytrack-cli/-/releases):

- **Debian 13 / 14, Ubuntu 24.04 / 26.04**: `sudo apt install ./python3-activitytrack_<version>-1-<distro>_all.deb ./activitytrack-cli_<version>-1-<distro>_all.deb`
  (the library from its own release);
- **Windows 10 / 11**: `activitytrack-cli-<version>-windows.zip`, `activitytrack.exe` in a folder of the PATH;
- **elsewhere**: `pip install` the library, then the tarball.

## Development

```sh
apt install python3-cryptography python3-keyring flake8
git clone https://gitlab.com/activitytrack/python3-activitytrack ../python3-activitytrack
export PYTHONPATH=$PWD/../python3-activitytrack
python3 -m activitytrack_cli --help
python3 -m unittest discover -s tests -t .
python3 scripts/strings.py --check
flake8 activitytrack_cli tests packaging/activitytrack
```

## License

GPL-3.0-or-later.
