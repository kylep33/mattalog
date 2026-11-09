# mattalog

sends stuff i downloaded to the right folder on mattbot. one python file, no pip anything, just `python3` and `rsync`.

## install

on a box with [kile-useful-files](https://github.com/kylep33/kile-useful-files), run `computer-setup/limnux_master_setup.sh` and tick mattalog. or run the step by itself:

```bash
~/dev/kile-useful-files/computer-setup/limnux_specific/install_mattalog.sh
```

by hand:

```bash
sudo apt install git python3 rsync
git clone git@github.com:kylep33/mattalog.git ~/dev/mattalog
mkdir -p ~/.local/bin
ln -sfn ~/dev/mattalog/mattalog.py ~/.local/bin/mattalog
```

it is a symlink, so `git pull` updates it. do not move the clone.

the box also needs ssh access to mattbot. check with `ssh kile@mattbot true`.

## use

```bash
mattalog books dune.epub foundation.epub neuromancer.pdf
mattalog shows ~/Downloads/some-show/
mattalog movies movie.mkv
mattalog photos *.jpg
mattalog --autoname books "very long anna's archive name.epub"
mattalog --settings
```

files and folders both work. originals stay where they are. anything already at the destination gets skipped and says `exists`, nothing gets overwritten. interrupted transfers pick back up if you rerun the same command.

## destinations

| name | goes to |
|---|---|
| `books` | `kile@mattbot:/srv/data/books/ingest` |
| `shows` | `kile@mattbot:/srv/data/media/shows` |
| `movies` | `kile@mattbot:/srv/data/media/movies` |
| `photos` | `kile@mattbot:/srv/data/media/photos` |

books go to `ingest` because that is the folder calibre-web-automated imports from.

## settings

`mattalog --settings` opens a menu. click a row or use arrows or j/k and enter to edit. d deletes, q quits.

new destinations are a name and a target, like `mattdev` and `kile@mattbot:~/dev`, then `mattalog mattdev thing.py` just works. a target can also be a local path.

servers are a name and a list of addresses tried in order. mattbot is `mattbot` (tailscale) then `192.168.1.88` (lan). add a second server the same way and point destinations at it.

saved to `~/.config/mattalog/config.json`. that file only exists once you change something, until then it uses the defaults above.

## --autoname

files only, folders keep their name.

1. epub metadata, if it is an epub and has a title
2. the anna's archive `Title -- Author -- Publisher -- ...` filename pattern
3. otherwise the name stays

you get `Dune - Frank Herbert.epub`. subtitles get dropped and only the first author is kept. it prints every rename, and your local file keeps its name.

## when mattbot is down

it says unreachable and why, for every address it tried:

```
mattalog: mattbot unreachable
  mattbot: name doesn't resolve: check Tailscale or the spelling
  192.168.1.88: host is up, but it rejected your SSH key (try ssh-copy-id)
  tailscale: down on this machine (try: sudo tailscale up)
```

it does not fail over to a different server. a movie landing on the wrong box splits the library.

## tests

```bash
python3 -m unittest test_mattalog
```
