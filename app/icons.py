"""Bibliotheque d'icones SVG inline (trait fin, style lineaire).

Aucune dependance externe : chaque icone est definie par son contenu SVG interne.
"""
from markupsafe import Markup

_ICONS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/>',
    "briefcase": ('<rect x="2.5" y="7" width="19" height="13" rx="2"/>'
                  '<path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/>'
                  '<path d="M2.5 12h19"/>'),
    "shield": ('<path d="M12 22s8-3.6 8-10V5.2L12 2 4 5.2V12c0 6.4 8 10 8 10z"/>'
               '<path d="M9 11.5l2 2 4-4.5"/>'),
    "key": ('<circle cx="7.5" cy="15.5" r="4.5"/>'
            '<path d="M10.7 12.3L21 2"/><path d="M18 5l3 3"/><path d="M15 8l2 2"/>'),
    "logout": ('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
               '<path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>'),
    "x": '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>',
    "pin": ('<path d="M21 10c0 7-9 13-9 13S3 17 3 10a9 9 0 0 1 18 0z"/>'
            '<circle cx="12" cy="10" r="3"/>'),
    "building": ('<rect x="4" y="3" width="16" height="18" rx="1"/>'
                 '<path d="M9 7h1.2M13.8 7H15M9 11h1.2M13.8 11H15M9 15h1.2M13.8 15H15"/>'
                 '<path d="M10 21v-3.2h4V21"/>'),
    "users": ('<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
              '<circle cx="9" cy="7" r="4"/>'
              '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>'
              '<path d="M16 3.13a4 4 0 0 1 0 7.75"/>'),
    "globe": ('<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/>'
              '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10'
              ' 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'),
    "ban": '<circle cx="12" cy="12" r="10"/><path d="M4.9 4.9l14.2 14.2"/>',
    "bookmark": '<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
    "download": ('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                 '<path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>'),
    "refresh": ('<path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>'
                '<path d="M3.5 9a9 9 0 0 1 14.85-3.36L23 10"/>'
                '<path d="M20.5 15a9 9 0 0 1-14.85 3.36L1 14"/>'),
    "external": ('<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
                 '<path d="M15 3h6v6"/><path d="M10 14L21 3"/>'),
    "chevron-left": '<path d="M15 18l-6-6 6-6"/>',
    "chevron-right": '<path d="M9 18l6-6-6-6"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "alert": ('<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0'
              ' 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
              '<path d="M12 9v4"/><path d="M12 17h.01"/>'),
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "calendar": ('<rect x="3" y="4" width="18" height="18" rx="2"/>'
                 '<path d="M16 2v4M8 2v4M3 10h18"/>'),
    "restore": ('<path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>'),
    "trash": ('<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
              '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'),
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "info": ('<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/>'
             '<path d="M12 8h.01"/>'),
    "arrow-right": ('<path d="M5 12h14"/><path d="M12 5l7 7-7 7"/>'),
    "euro": ('<path d="M18.5 5.5A8.5 8.5 0 1 0 18.5 18.5"/>'
             '<path d="M3 10h9M3 14h9"/>'),
    "eye-off": ('<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8'
                'a18.45 18.45 0 0 1 5.06-5.94"/>'
                '<path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0'
                ' 1-2.16 3.19"/>'
                '<path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><path d="M1 1l22 22"/>'),
    "layers": ('<path d="M12 2L2 7l10 5 10-5-10-5z"/>'
               '<path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>'),
}


def icon(name: str, size: int = 16, cls: str = "") -> Markup:
    body = _ICONS.get(name)
    if body is None:
        return Markup("")
    classes = f"icon{(' ' + cls) if cls else ''}"
    # Balises issues du dictionnaire _ICONS ci-dessus, jamais de donnee externe.
    return Markup(  # noqa: S704
        f'<svg class="{classes}" width="{size}" height="{size}" viewBox="0 0 24 24"'
        f' fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"'
        f' stroke-linejoin="round" aria-hidden="true">{body}</svg>'
    )
