"""Resolution DNS d'un nom d'hote, isolee dans son propre processus.

Pourquoi un sous-processus : `socket.getaddrinfo` **n'applique pas**
`socket.setdefaulttimeout`. Lancee dans un thread, une resolution qui ne repond
pas (reseau filtre, serveur DNS muet) l'occupe indefiniment, et rien ne peut
l'interrompre de l'exterieur. Le pool de threads finissait donc par se saturer,
et la detection de site se degradait en « inconnu » sans le dire.

Ce programme est execute par `detect._resout` avec un delai maximal : passe
l'echeance, le processus est tue par l'appelant, le thread est rendu.

Sortie : 0 si le nom resout, 1 sinon, 2 si l'appel est incorrect.
"""
import socket
import sys

TIMEOUT = 1.5


def main(argv) -> int:
    if len(argv) != 2:
        return 2
    try:
        socket.setdefaulttimeout(TIMEOUT)
        infos = socket.getaddrinfo(argv[1], 443, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return 1
    return 0 if infos else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
