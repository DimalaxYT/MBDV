/* Balise Prospection : interactions (panneau fiche, masquage, suivi, toasts) */
(function () {
  "use strict";

  var CSRF = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  // Mode apercu embarque : la session voyage dans l'URL (cookies tiers refuses).
  var SESSION = (document.querySelector('meta[name="session-token"]') || {}).content || "";
  var DEMO = document.body.dataset.demo === "1";

  function withSession(url) {
    var ajouts = [];
    if (SESSION && url.indexOf("_s=") < 0) ajouts.push("_s=" + encodeURIComponent(SESSION));
    if (DEMO && url.indexOf("demo=") < 0) ajouts.push("demo=1");
    if (!ajouts.length) return url;
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + ajouts.join("&");
  }

  // ------------------------------------------------------------------
  // Utilitaires
  // ------------------------------------------------------------------
  function post(url, data) {
    return fetch(withSession(url), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": CSRF,
      },
      body: JSON.stringify(data || {}),
    }).then(function (resp) {
      return resp.json().catch(function () {
        return { ok: false, error: "Réponse invalide du serveur." };
      }).then(function (body) {
        body._status = resp.status;
        return body;
      });
    });
  }

  function toast(message, type) {
    var root = document.getElementById("toast-root");
    if (!root) return;
    var el = document.createElement("div");
    el.className = "toast" + (type ? " toast-" + type : "");
    el.textContent = message;
    root.appendChild(el);
    setTimeout(function () {
      el.classList.add("leaving");
      setTimeout(function () { el.remove(); }, 280);
    }, 3800);
  }

  function getRow(siren) {
    return document.querySelector('tr[data-siren="' + siren + '"]');
  }

  function snapshotFor(el) {
    // Priorite : ligne du tableau, sinon panneau fiche ouvert
    var row = el.closest("tr");
    if (row && row.dataset.snapshot) {
      try { return JSON.parse(row.dataset.snapshot); } catch (e) { return null; }
    }
    var over = document.querySelector(".slideover");
    if (over && over.dataset.snapshot) {
      try { return JSON.parse(over.dataset.snapshot); } catch (e) { return null; }
    }
    return null;
  }

  // ------------------------------------------------------------------
  // Panneau lateral (fiche entreprise)
  // ------------------------------------------------------------------
  var slideoverRoot = document.getElementById("slideover-root");

  function closeSlideover() {
    if (slideoverRoot) slideoverRoot.innerHTML = "";
  }

  function slideoverOuverte() {
    return !!document.querySelector(".slideover");
  }

  function openSlideover(html) {
    slideoverRoot.innerHTML =
      '<div class="slideover-backdrop" data-close-slideover></div>' +
      '<div class="slideover-wrap">' + html + "</div>";
  }

  function loadDetail(siren) {
    return fetch(withSession("/entreprise/" + siren + "/detail"), {
      headers: { "X-CSRF-Token": CSRF },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("http");
        return r.text();
      })
      .then(openSlideover)
      .catch(function () {
        toast("Impossible de charger la fiche entreprise.", "error");
      });
  }

  function refreshSlideover(siren) {
    if (slideoverOuverte()) loadDetail(siren);
  }

  function updateRowBadge(siren, badgeHtml) {
    var row = getRow(siren);
    if (row) {
      var cell = row.querySelector(".col-site");
      if (cell) cell.innerHTML = badgeHtml;
    }
  }

  // ------------------------------------------------------------------
  // Masquage (modale avec motif obligatoire)
  // ------------------------------------------------------------------
  var modalRoot = document.getElementById("modal-root");

  function closeHideModal() {
    if (modalRoot) modalRoot.innerHTML = "";
  }

  function openHideModal(siren, nom, snapshot) {
    var raisons = window.MBDV_RAISONS || [];
    var options = raisons.map(function (r) {
      return '<option value="' + r.replace(/"/g, "&quot;") + '">' + r + "</option>";
    }).join("");
    modalRoot.innerHTML =
      '<div class="modal-backdrop" data-close-modal>' +
      '<div class="modal" role="dialog" aria-modal="true">' +
      "<h3>Masquer cette entreprise</h3>" +
      '<p class="modal-sub"><strong>' + escapeHtml(nom) + "</strong> (SIREN " + siren + ")<br>" +
      "Elle ne sera plus proposée dans les résultats de recherche. Le motif est visible" +
      " dans le panel staff avec l'auteur et la date.</p>" +
      '<div class="stack">' +
      '<div class="field"><label for="hide-raison">Raison du masquage</label>' +
      '<select id="hide-raison"><option value="" selected disabled>Choisir une raison</option>' +
      options + "</select></div>" +
      '<div class="field"><label for="hide-details">Precisions (facultatif)</label>' +
      '<textarea id="hide-details" placeholder="Contexte utile pour l\'equipe..."></textarea></div>' +
      "</div>" +
      '<div class="modal-error" id="hide-error"></div>' +
      '<div class="modal-actions">' +
      '<button type="button" class="btn btn-ghost" data-close-modal>Annuler</button>' +
      '<button type="button" class="btn btn-danger" id="hide-confirm">' +
      "Masquer l'entreprise</button>" +
      "</div></div></div>";

    document.getElementById("hide-confirm").addEventListener("click", function () {
      var raison = document.getElementById("hide-raison").value;
      var details = document.getElementById("hide-details").value.trim();
      var errBox = document.getElementById("hide-error");
      if (!raison) {
        errBox.innerHTML =
          '<div class="banner banner-error">Choisissez une raison avant de valider.</div>';
        return;
      }
      post("/api/masquer", {
        siren: siren, nom: nom, raison: raison, details: details, snapshot: snapshot,
      }).then(function (body) {
        if (!body.ok) {
          errBox.innerHTML =
            '<div class="banner banner-error">' + escapeHtml(body.error || "Erreur.") + "</div>";
          return;
        }
        closeHideModal();
        closeSlideover();
        var row = getRow(siren);
        if (row) {
          row.classList.add("row-removing");
          setTimeout(function () { row.remove(); }, 260);
        }
        toast("Entreprise masquée — motif : " + raison, "ok");
      });
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------
  function actionMasquer(el) {
    var snap = snapshotFor(el) || {};
    var over = document.querySelector(".slideover");
    var row = el.closest("tr");
    var siren = (row && row.dataset.siren) || (snap && snap.siren) ||
      (over && over.dataset.siren);
    var nom = (snap && snap.nom) ||
      (row && row.querySelector(".row-name") && row.querySelector(".row-name").textContent) ||
      (over && over.querySelector("h2") && over.querySelector("h2").textContent) || "";
    if (!siren) { toast("Entreprise introuvable.", "error"); return; }
    openHideModal(siren, nom.trim(), snap);
  }

  function actionSuivre(el) {
    var snap = snapshotFor(el);
    if (!snap) { toast("Contexte indisponible.", "error"); return; }
    post("/api/suivre", { snapshot: snap }).then(function (body) {
      if (!body.ok) { toast(body.error || "Erreur.", "error"); return; }
      var row = getRow(snap.siren);
      if (row) {
        var btn = row.querySelector(".follow-btn");
        if (btn) btn.classList.toggle("is-on", body.tracked);
        var cell = row.querySelector(".col-statut");
        if (cell) {
          cell.innerHTML = body.tracked
            ? '<span class="badge st-a_contacter">À contacter</span>'
            : '<span class="dash">&mdash;</span>';
        }
      }
      toast(body.tracked
        ? snap.nom + " ajoutée au portefeuille"
        : snap.nom + " retirée du portefeuille", "ok");
      if (slideoverOuverte()) refreshSlideover(snap.siren);
      if (document.body.dataset.page === "portefeuille") {
        setTimeout(function () { window.location.reload(); }, 200);
      }
    });
  }

  function actionRecheck(el) {
    var snap = snapshotFor(el) || {};
    var row = el.closest("tr");
    var siren = (row && row.dataset.siren) || snap.siren;
    if (!siren) return;
    el.disabled = true;
    post("/api/recheck", {
      siren: siren,
      nom: snap.nom || (el.dataset.nom || ""),
      enseigne: snap.enseigne || (el.dataset.enseigne || ""),
    }).then(function (body) {
      el.disabled = false;
      if (!body.ok) { toast(body.error || "Vérification impossible.", "error"); return; }
      updateRowBadge(siren, body.badge_html);
      toast(body.site.status === "site"
        ? "Site détecté : " + body.site.domain
        : body.site.status === "aucun" ? "Aucun site détecté" : "Vérification incomplete",
        body.site.status === "site" ? "" : "ok");
      refreshSlideover(siren);
    });
  }

  function actionOverride(el) {
    var over = document.querySelector(".slideover");
    if (!over) return;
    var snap = JSON.parse(over.dataset.snapshot || "{}");
    var valeur = el.dataset.override === "sans" ? "sans" : "";
    post("/api/site-override", { siren: snap.siren, value: valeur || null, nom: snap.nom })
      .then(function (body) {
        if (!body.ok) { toast(body.error || "Erreur.", "error"); return; }
        updateRowBadge(snap.siren, body.badge_html);
        toast(valeur ? "Absence de site confirmée manuellement" : "Confirmation annulée", "ok");
        refreshSlideover(snap.siren);
      });
  }

  function actionRestore(id) {
    post("/api/retablir", { id: id }).then(function (body) {
      if (!body.ok) { toast(body.error || "Erreur.", "error"); return; }
      toast("Entreprise réaffichée dans les résultats", "ok");
      closeSlideover();
      if (document.body.dataset.page === "staff") {
        setTimeout(function () { window.location.reload(); }, 200);
      }
    });
  }

  function actionStatut(select) {
    var siren = select.dataset.statut;
    post("/api/statut", { siren: siren, statut: select.value }).then(function (body) {
      if (!body.ok) { toast(body.error || "Erreur.", "error"); return; }
      var row = getRow(siren);
      if (row) {
        var cell = row.querySelector(".col-statut .badge");
        if (cell) {
          cell.className = "badge st-" + select.value;
          var label = select.options[select.selectedIndex].textContent;
          cell.textContent = label;
        }
      }
      toast("Statut mis à jour", "ok");
      refreshSlideover(siren);
    });
  }

  // ------------------------------------------------------------------
  // Repertoire : ajouter une entreprise, ranger ses livrables
  // ------------------------------------------------------------------
  function openAjouterModal() {
    if (!modalRoot) return;
    modalRoot.innerHTML =
      '<div class="modal-backdrop" data-close-modal>' +
      '<div class="modal" role="dialog" aria-modal="true">' +
      "<h3>Ajouter une entreprise</h3>" +
      '<p class="modal-sub">Cherchez par nom, enseigne ou SIREN : la fiche est reprise de ' +
      "la base officielle (adresse, activité, dirigeant).</p>" +
      '<div class="stack">' +
      '<div class="field"><label for="annuaire-q">Nom, enseigne ou SIREN</label>' +
      '<div class="recherche-ligne">' +
      '<input id="annuaire-q" type="search" spellcheck="false" ' +
      'placeholder="ex. coiffure Saint-Nazaire, ou 848902672">' +
      '<button type="button" class="btn btn-ink" id="annuaire-chercher">Rechercher</button>' +
      "</div></div>" +
      '<div id="annuaire-resultats" class="annuaire-resultats"></div>' +
      '<details class="annuaire-manuel"><summary>Saisie manuelle (si la base officielle ' +
      "ne répond pas)</summary>" +
      '<div class="stack stack-serre">' +
      '<div class="field"><label for="manuel-siren">SIREN <span class="label-note">' +
      "(9 chiffres)</span></label>" +
      '<input id="manuel-siren" inputmode="numeric" maxlength="9" placeholder="123456789"></div>' +
      '<div class="field"><label for="manuel-nom">Nom de l&#39;entreprise</label>' +
      '<input id="manuel-nom" placeholder="Nom commercial ou raison sociale"></div>' +
      '<div class="field"><label for="manuel-commune">Commune <span class="label-note">' +
      "(facultatif)</span></label><input id=\"manuel-commune\"></div>" +
      '<div class="field"><label for="manuel-activite">Activité <span class="label-note">' +
      "(facultatif)</span></label><input id=\"manuel-activite\"></div>" +
      '<button type="button" class="btn btn-ghost" id="manuel-ajouter">' +
      "Ajouter cette entreprise</button>" +
      "</div></details>" +
      "</div>" +
      '<div class="modal-error" id="annuaire-error"></div>' +
      '<div class="modal-actions">' +
      '<button type="button" class="btn btn-ghost" data-close-modal>Fermer</button>' +
      "</div></div></div>";

    var champ = document.getElementById("annuaire-q");
    champ.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); chercherAnnuaire(); }
    });
    document.getElementById("annuaire-chercher").addEventListener("click", chercherAnnuaire);
    document.getElementById("manuel-ajouter").addEventListener("click", function () {
      ajouterAuRepertoire({
        siren: document.getElementById("manuel-siren").value,
        nom: document.getElementById("manuel-nom").value,
        commune: document.getElementById("manuel-commune").value,
        activite: document.getElementById("manuel-activite").value,
      });
    });
    champ.focus();
  }

  function chercherAnnuaire() {
    var q = document.getElementById("annuaire-q").value.trim();
    var zone = document.getElementById("annuaire-resultats");
    var erreur = document.getElementById("annuaire-error");
    if (erreur) erreur.innerHTML = "";
    if (q.length < 2) {
      zone.innerHTML = '<p class="row-sub">Indiquez au moins deux caractères.</p>';
      return;
    }
    zone.innerHTML = '<p class="row-sub">Recherche dans la base officielle…</p>';
    var url = withSession("/api/annuaire") + (withSession("/api/annuaire").indexOf("?") >= 0 ? "&" : "?")
      + "q=" + encodeURIComponent(q);
    fetch(url, { headers: { "X-CSRF-Token": CSRF } })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (!body.ok) {
          zone.innerHTML = '<p class="row-sub">' + escapeHtml(body.error || "Recherche impossible.") + "</p>";
          if (body.indisponible) {
            var manuel = document.querySelector(".annuaire-manuel");
            if (manuel) manuel.open = true;
          }
          return;
        }
        if (!body.resultats.length) {
          zone.innerHTML = '<p class="row-sub">Aucune entreprise trouvée. Essayez un autre ' +
            "nom, ou la saisie manuelle ci-dessous.</p>";
          return;
        }
        zone.innerHTML = body.resultats.map(function (r) {
          var details = [r.commune, r.naf_label, r.effectif].filter(Boolean).join(" · ");
          return '<article class="annuaire-item' + (r.deja ? " is-deja" : "") + '">' +
            "<div><span class=\"annuaire-nom\">" + escapeHtml(r.nom || "") + "</span>" +
            '<span class="row-sub"><code class="mono">' + escapeHtml(r.siren || "") + "</code>" +
            (details ? " · " + escapeHtml(details) : "") + "</span></div>" +
            (r.deja
              ? '<span class="badge">déjà dans le répertoire</span>'
              : '<button type="button" class="btn btn-ink btn-sm" data-ajouter-siren="' +
                escapeHtml(r.siren || "") + '">Ajouter</button>') +
            "</article>";
        }).join("");
      })
      .catch(function () {
        zone.innerHTML = '<p class="row-sub">Recherche impossible pour le moment.</p>';
      });
  }

  function ajouterAuRepertoire(charge) {
    var erreur = document.getElementById("annuaire-error");
    var bouton = document.querySelector("#annuaire-manuel .btn, #annuaire-resultats .btn");
    if (bouton) bouton.disabled = true;
    post("/api/suivi/ajouter", charge).then(function (body) {
      if (bouton) bouton.disabled = false;
      if (!body.ok) {
        if (erreur) erreur.innerHTML =
          '<div class="banner banner-error">' + escapeHtml(body.error || "Ajout impossible.") + "</div>";
        return;
      }
      closeHideModal();
      toast("Entreprise ajoutée au répertoire", "ok");
      // La ligne apparait dans le tableau : on recharge la page courante.
      window.location.reload();
    });
  }

  function openLivrablesModal(siren, nom, vitrine, dossier, zipLien) {
    if (!modalRoot) return;
    ligneAppel = document.querySelector('tr[data-siren="' + siren + '"]');
    modalRoot.innerHTML =
      '<div class="modal-backdrop" data-close-modal>' +
      '<div class="modal" role="dialog" aria-modal="true">' +
      "<h3>Livrables</h3>" +
      '<p class="modal-sub"><strong>' + escapeHtml(nom || "") + "</strong> (SIREN " + siren + ")<br>" +
      "Le dossier du site, l'archive .zip et l'URL de la vitrine restent accessibles " +
      "à l'équipe depuis le répertoire.</p>" +
      '<div class="stack">' +
      '<div class="field"><label for="liv-dossier">Dossier du site ' +
      '<span class="label-note">(chemin ou lien)</span></label>' +
      '<input id="liv-dossier" value="' + escapeHtml(dossier || "") + '" ' +
      'placeholder="ex.\\\\projets\\\\mbdv\\\\vitrines\\\\dupont ou https://…"></div>' +
      '<div class="field"><label for="liv-zip">Archive .zip ' +
      '<span class="label-note">(dépôt ci-dessous, ou lien vers une archive)</span></label>' +
      '<input id="liv-zip" value="' + escapeHtml(zipLien || "") + '" placeholder="https://…/site.zip">' +
      '<input id="liv-fichier" type="file" accept=".zip,application/zip">' +
      '<span class="row-sub" id="liv-zip-actuel"></span></div>' +
      '<div class="field"><label for="liv-vitrine">URL de vitrine</label>' +
      '<input id="liv-vitrine" value="' + escapeHtml(vitrine || "") + '" ' +
      'placeholder="https://vitrine.exemple.fr"></div>' +
      "</div>" +
      '<div class="modal-error" id="liv-error"></div>' +
      '<div class="modal-actions">' +
      '<button type="button" class="btn btn-ghost" data-close-modal>Annuler</button>' +
      '<button type="button" class="btn btn-ink" id="liv-confirm">Enregistrer</button>' +
      "</div></div></div>";

    var actuel = document.getElementById("liv-zip-actuel");
    if (ligneAppel && ligneAppel.querySelector(".livrables .chip[href*='/livrables/zip/']")) {
      actuel.textContent = "Une archive est déjà déposée : en choisir une autre la remplacera.";
    }
    document.getElementById("liv-confirm").addEventListener("click", function () {
      enregistrerLivrables(siren);
    });
  }

  function enregistrerLivrables(siren) {
    var bouton = document.getElementById("liv-confirm");
    var erreur = document.getElementById("liv-error");
    var champ = document.getElementById("liv-fichier");
    var fichier = champ && champ.files && champ.files[0];
    if (bouton) bouton.disabled = true;
    if (erreur) erreur.innerHTML = "";

    var depot = fichier ? deposerArchive(siren, fichier) : Promise.resolve({ ok: true });
    depot.then(function (reponse) {
      if (!reponse.ok) {
        if (erreur) erreur.innerHTML = '<div class="banner banner-error">' +
          escapeHtml(reponse.error || "Dépôt impossible.") + "</div>";
        if (bouton) bouton.disabled = false;
        return;
      }
      return post("/api/suivi/livrables", {
        siren: siren,
        dossier_site: document.getElementById("liv-dossier").value,
        zip_lien: document.getElementById("liv-zip").value,
        url_vitrine: document.getElementById("liv-vitrine").value,
      }).then(function (body) {
        if (!body.ok) {
          if (erreur) erreur.innerHTML = '<div class="banner banner-error">' +
            escapeHtml(body.error || "Enregistrement impossible.") + "</div>";
          if (bouton) bouton.disabled = false;
          return;
        }
        appliquerEtatAppel(ligneAppel, body);
        closeHideModal();
        toast(reponse.message || "Livrables enregistrés", "ok");
      });
    });
  }

  // Le .zip n'est pas du JSON : envoi multipart, meme jeton CSRF que le reste.
  function deposerArchive(siren, fichier) {
    var donnees = new FormData();
    donnees.append("siren", siren);
    donnees.append("fichier", fichier);
    return fetch(withSession("/suivi/livrables/zip"), {
      method: "POST",
      headers: { "X-CSRF-Token": CSRF },
      body: donnees,
    }).then(function (r) {
      return r.json().catch(function () {
        return { ok: false, error: "Réponse invalide du serveur." };
      });
    });
  }

  // ------------------------------------------------------------------
  // Appels a passer : une ligne par entreprise, une modale pour l'appel
  // ------------------------------------------------------------------
  var ligneAppel = null;

  function actionPrendre(bouton) {
    var ligne = bouton.closest("tr");
    var prendre = bouton.dataset.mine !== "1";
    bouton.disabled = true;
    post("/api/suivi/prendre", { siren: bouton.getAttribute("data-prendre"), prendre: prendre })
      .then(function (body) {
        appliquerEtatAppel(ligne, body);
        toast(prendre ? "Cette entreprise vous est attribuée" : "Remise dans la file commune", "ok");
      });
  }

  // Le serveur renvoie tous les libelles deja calcules : on les ecrit, on ne
  // recompose rien ici.
  function appliquerEtatAppel(ligne, etat) {
    if (!ligne || !etat || !etat.ok) return;
    var referent = ligne.querySelector("[data-referent]");
    if (referent && etat.referent_html) referent.innerHTML = etat.referent_html;
    var historique = ligne.querySelector("[data-dernier]");
    if (historique) historique.textContent = etat.dernier_appel;
    var compteur = ligne.querySelector("[data-appels]");
    if (compteur) compteur.textContent = etat.appels_label;
    var relance = ligne.querySelector("[data-relance]");
    if (relance) {
      relance.textContent = etat.urgence_label;
      relance.className = "badge u-" + etat.urgence;
    }
    var badge = ligne.querySelector("[data-site-badge]");
    if (badge && etat.badge_html) badge.parentNode.innerHTML = etat.badge_html;
    var livrables = ligne.querySelector("[data-livrables-cellule]");
    if (livrables && etat.livrables_html) livrables.innerHTML = etat.livrables_html;
    // La ligne garde l'etat courant : rouvrir la modale repart de la bonne note.
    try {
      var memo = JSON.parse(ligne.dataset.snapshot || "{}");
      memo.note = etat.note;
      memo.relance_le = etat.relance_le;
      memo.pris_par = etat.pris_par;
      ligne.dataset.snapshot = JSON.stringify(memo);
    } catch (e) { /* instantane illisible : sans consequence */ }
  }

  function openAppelModal(siren, nom, note, relance) {
    if (!modalRoot) return;
    ligneAppel = document.querySelector('tr[data-siren="' + siren + '"]');
    modalRoot.innerHTML =
      '<div class="modal-backdrop" data-close-modal>' +
      '<div class="modal" role="dialog" aria-modal="true">' +
      "<h3>Appel passé</h3>" +
      '<p class="modal-sub"><strong>' + escapeHtml(nom || "") + "</strong> (SIREN " + siren + ")<br>" +
      "Le compte rendu et la date du prochain appel restent visibles par l'équipe.</p>" +
      '<div class="stack">' +
      '<div class="field"><label for="appel-note">Compte rendu de l\'appel</label>' +
      '<textarea id="appel-note" rows="3" ' +
      'placeholder="Qui a répondu, ce qui s\'est dit, la suite à donner...">' +
      escapeHtml(note || "") + "</textarea></div>" +
      '<div class="field"><label for="appel-relance">Prochain appel ' +
      '<span class="label-note">(facultatif)</span></label>' +
      '<input id="appel-relance" type="date" value="' + escapeHtml(relance || "") + '"></div>' +
      "</div>" +
      '<div class="modal-error" id="appel-error"></div>' +
      '<div class="modal-actions">' +
      '<button type="button" class="btn btn-ghost" data-close-modal>Annuler</button>' +
      '<button type="button" class="btn btn-ink" id="appel-confirm">' +
      "Enregistrer l'appel</button>" +
      "</div></div></div>";

    document.getElementById("appel-confirm").addEventListener("click", function () {
      envoyerAppel(siren, document.getElementById("appel-note").value,
                   document.getElementById("appel-relance").value);
    });
  }

  function envoyerAppel(siren, note, relance) {
    var bouton = document.getElementById("appel-confirm");
    if (bouton) bouton.disabled = true;
    post("/api/suivi/appel", { siren: siren, note: note, relance_le: relance })
      .then(function (body) {
        if (!body.ok) {
          var zone = document.getElementById("appel-error");
          if (zone) zone.innerHTML = '<div class="banner banner-error">' +
            escapeHtml(body.error || "Enregistrement impossible.") + "</div>";
          if (bouton) bouton.disabled = false;
          return;
        }
        appliquerEtatAppel(ligneAppel, body);
        closeHideModal();
        toast("Appel enregistré", "ok");
      });
  }

  // ------------------------------------------------------------------
  // Session dans l'URL (apercu embarque) : liens et formulaires
  // ------------------------------------------------------------------
  function completable(form) {
    if (SESSION && !form.querySelector('input[name="_s"]')) {
      var champ = document.createElement("input");
      champ.type = "hidden";
      champ.name = "_s";
      champ.value = SESSION;
      form.appendChild(champ);
    }
    if (DEMO && !form.querySelector('input[name="demo"]')) {
      var marqueur = document.createElement("input");
      marqueur.type = "hidden";
      marqueur.name = "demo";
      marqueur.value = "1";
      form.appendChild(marqueur);
    }
  }

  document.addEventListener("submit", function (ev) {
    if (ev.target && ev.target.tagName === "FORM") completable(ev.target);
  }, true);

  document.addEventListener("click", function (ev) {
    if ((!SESSION && !DEMO) || !ev.target || !ev.target.closest) return;
    var lien = ev.target.closest("a[href]");
    if (!lien) return;
    var href = lien.getAttribute("href") || "";
    if (href.charAt(0) !== "/" || href.indexOf("_s=") >= 0) return;
    lien.setAttribute("href", withSession(href));
  }, true);

  // ------------------------------------------------------------------
  // Animations d'apparition et compteurs (page d'accueil)
  // ------------------------------------------------------------------
  function animerCompteur(el) {
    var cible = parseInt(el.dataset.count, 10);
    if (isNaN(cible)) return;
    if (reduitMouvement() || cible === 0) { el.textContent = cible; return; }
    var debut = null;
    var duree = 750;
    function pas(temps) {
      if (debut === null) debut = temps;
      var avance = Math.min(1, (temps - debut) / duree);
      var adouci = 1 - Math.pow(1 - avance, 3);          // ease-out cubique
      el.textContent = Math.round(cible * adouci);
      if (avance < 1) window.requestAnimationFrame(pas);
    }
    window.requestAnimationFrame(pas);
  }

  function reduitMouvement() {
    return typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function reveler(el) {
    if (el.classList.contains("is-visible")) return;
    el.classList.add("is-visible");
    var compteurs = el.querySelectorAll("[data-count]");
    for (var i = 0; i < compteurs.length; i++) animerCompteur(compteurs[i]);
  }

  // ------------------------------------------------------------------
  // Changement de page : reponse immediate au clic, puis fondu du nouveau
  // contenu. Sans cela, entre le clic et l'arrivee de la page, rien ne bouge
  // et l'attente parait longue.
  // ------------------------------------------------------------------
  var barreDeProgression = null;

  function barre() {
    if (!barreDeProgression) {
      barreDeProgression = document.createElement("div");
      barreDeProgression.className = "progress";
      barreDeProgression.setAttribute("aria-hidden", "true");
      document.body.appendChild(barreDeProgression);
    }
    return barreDeProgression;
  }

  function navigationEnCours() {
    return document.body.classList.contains("page-en-cours");
  }

  function transitionNative() {
    return document.documentElement.classList.contains("vt");
  }

  function demarrerNavigation(lien) {
    if (navigationEnCours()) return;
    document.body.classList.add("page-en-cours");
    // Sans transition native, on anime la sortie du contenu puis on part : 150 ms,
    // le temps d'un fondu, imperceptible mais la page ne "saute" plus.
    if (!transitionNative() && lien && !reduitMouvement()) {
      var contenu = document.querySelector(".main");
      if (contenu) {
        contenu.classList.add("sortie");
        window.setTimeout(function () { window.location.href = lien.href; }, 150);
      }
    }
    var trait = barre();
    trait.classList.add("is-actif");
    trait.style.width = "8%";
    // Petite avancee rapide puis une progression lente : la barre ne se bloque
    // jamais a 100 % tant que la nouvelle page n'est pas la.
    window.setTimeout(function () { trait.style.width = "55%"; }, 60);
    window.setTimeout(function () { trait.style.width = "88%"; }, 450);
    if (lien) {
      // Le lien touche se marque actif immediatement (retour visuel du clic).
      var item = lien.closest(".nav-item");
      if (item) {
        var ancien = document.querySelector(".nav-item.is-en-cours");
        if (ancien && ancien !== item) ancien.classList.remove("is-en-cours");
        item.classList.add("is-en-cours");
      }
    }
  }

  function initTransitionsDePage() {
    document.addEventListener("click", function (ev) {
      if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey
          || ev.altKey) return;
      var lien = ev.target.closest ? ev.target.closest("a[href]") : null;
      if (!lien || lien.target || lien.hasAttribute("download")
          || lien.dataset.sansTransition !== undefined) return;
      if (lien.getAttribute("href") === "#") return;
      try {
        if (new URL(lien.href, window.location.href).origin !== window.location.origin) return;
      } catch (e) { return; }
      if (ev.target.closest("[data-detail],[data-close-slideover],[data-close-modal]")) return;
      demarrerNavigation(lien);
    });

    // Envoi d'un formulaire (recherche, connexion, action) : meme retour.
    document.addEventListener("submit", function () {
      demarrerNavigation(null);
    }, true);

    // Retour arriere (page restauree depuis le cache du navigateur) : on remet
    // tout a zero, sinon la barre resterait affichee.
    window.addEventListener("pageshow", function () {
      document.body.classList.remove("page-en-cours");
      var trait = document.querySelector(".progress");
      if (trait) {
        trait.classList.remove("is-actif");
        trait.style.width = "0";
      }
      var item = document.querySelector(".nav-item.is-en-cours");
      if (item) item.classList.remove("is-en-cours");
    });
  }

  // ------------------------------------------------------------------
  // Retour visuel immediat : des qu'un formulaire part, le bouton le dit.
  // Une recherche interroge plusieurs pages de l'API : sans cela, le clic
  // semble sans effet pendant quelques secondes.
  // ------------------------------------------------------------------
  var SECOURS_BOUTON = 20000;      // au dela, un bouton bloque se libere seul

  function initEtatsDeSoumission() {
    var formulaires = document.querySelectorAll("form[data-chargement]");
    for (var i = 0; i < formulaires.length; i++) {
      (function (form) {
        form.addEventListener("submit", function () {
          var bouton = form.querySelector('button[type="submit"]');
          if (!bouton || bouton.disabled) return;
          bouton.classList.add("is-loading");
          bouton.disabled = true;
          // Filet de securite : si la navigation est annulee, le bouton revient.
          window.setTimeout(function () {
            bouton.classList.remove("is-loading");
            bouton.disabled = false;
          }, SECOURS_BOUTON);
        });
      })(formulaires[i]);
    }
    // Retour depuis l'historique : on remet les boutons dans leur etat normal.
    window.addEventListener("pageshow", function () {
      var boutons = document.querySelectorAll(".is-loading");
      for (var j = 0; j < boutons.length; j++) {
        boutons[j].classList.remove("is-loading");
        boutons[j].disabled = false;
      }
    });
  }

  function preparerAnimations() {
    var elements = document.querySelectorAll(".reveal");
    if (!elements.length) return;
    // decalage progressif pour un effet de vague plutot qu'un bloc qui saute
    for (var i = 0; i < elements.length; i++) {
      var rang = elements[i].parentNode ? Array.prototype.indexOf.call(
        elements[i].parentNode.children, elements[i]) : 0;
      elements[i].style.setProperty("--delai", Math.min(rang, 6) * 70 + "ms");
    }
    if (reduitMouvement() || !("IntersectionObserver" in window)) {
      for (var j = 0; j < elements.length; j++) reveler(elements[j]);
      return;
    }
    var observateur = new IntersectionObserver(function (entrees) {
      entrees.forEach(function (entree) {
        if (entree.isIntersecting) {
          reveler(entree.target);
          observateur.unobserve(entree.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.1 });
    for (var k = 0; k < elements.length; k++) observateur.observe(elements[k]);
  }

  // ------------------------------------------------------------------
  // Theme clair / sombre
  // ------------------------------------------------------------------
  function appliquerTheme(sombre) {
    document.documentElement.dataset.theme = sombre ? "dark" : "light";
    try { localStorage.setItem("mbdv-theme", sombre ? "dark" : "light"); } catch (e) { /* ignore */ }
  }

  document.addEventListener("click", function (ev) {
    if (!ev.target.closest || !ev.target.closest("[data-theme-toggle]")) return;
    appliquerTheme(document.documentElement.dataset.theme !== "dark");
  });

  // Si aucun choix n'a ete fait, suivre les changements de preference systeme.
  if (window.matchMedia) {
    var requete = window.matchMedia("(prefers-color-scheme: dark)");
    var suivre = function (ev) {
      var choix = null;
      try { choix = localStorage.getItem("mbdv-theme"); } catch (e) { choix = null; }
      if (!choix) document.documentElement.dataset.theme = ev.matches ? "dark" : "light";
    };
    if (requete.addEventListener) requete.addEventListener("change", suivre);
  }

  // ------------------------------------------------------------------
  // Delegation d'evenements
  // ------------------------------------------------------------------
  // Un clic de fermeture ne compte que s'il vise vraiment le fond (ou le bouton
  // prevu pour). Le fond porte data-close-modal et entoure tout le contenu : sans
  // ce garde-fou, le premier clic dans la modale (choix de la raison, saisie d'une
  // precision) etait pris pour un clic sur le fond et refermait la modale.
  function clicSurLeFond(ev) {
    var porteur = ev.target.closest("[data-close-slideover],[data-close-modal]");
    if (!porteur) return false;
    if (ev.target === porteur) return true;
    return !ev.target.closest(".modal, .slideover");
  }

  document.addEventListener("click", function (ev) {
    var target = ev.target.closest("[data-detail],[data-hide],[data-follow],[data-recheck],[data-override],[data-restore],[data-prendre],[data-appel],[data-ajouter],[data-ajouter-siren],[data-livrables],[data-close-slideover],[data-close-modal]");
    if (!target) return;

    if (target.hasAttribute("data-close-slideover") || target.hasAttribute("data-close-modal")) {
      if (!clicSurLeFond(ev)) return;
      ev.preventDefault();
      closeSlideover();
      closeHideModal();
      return;
    }
    if (target.hasAttribute("data-detail")) {
      ev.preventDefault();
      loadDetail(target.getAttribute("data-detail"));
      return;
    }
    if (target.hasAttribute("data-hide")) { actionMasquer(target); return; }
    if (target.hasAttribute("data-follow")) { actionSuivre(target); return; }
    if (target.hasAttribute("data-recheck")) { actionRecheck(target); return; }
    if (target.hasAttribute("data-override")) { actionOverride(target); return; }
    if (target.hasAttribute("data-restore")) { actionRestore(target.getAttribute("data-restore")); return; }
    if (target.hasAttribute("data-ajouter")) { openAjouterModal(); return; }
    if (target.hasAttribute("data-ajouter-siren")) {
      target.disabled = true;
      ajouterAuRepertoire({ siren: target.getAttribute("data-ajouter-siren") });
      return;
    }
    if (target.hasAttribute("data-livrables")) {
      var ligneLiv = target.closest("tr");
      openLivrablesModal(target.getAttribute("data-livrables"),
                         (ligneLiv && ligneLiv.querySelector(".row-name") || {}).textContent,
                         target.getAttribute("data-vitrine"), target.getAttribute("data-dossier"),
                         target.getAttribute("data-ziplien"));
      return;
    }
    if (target.hasAttribute("data-prendre")) { actionPrendre(target); return; }
    if (target.hasAttribute("data-appel")) {
      var ligne = target.closest("tr");
      var memoire = {};
      try { memoire = JSON.parse((ligne && ligne.dataset.snapshot) || "{}"); } catch (e) { memoire = {}; }
      openAppelModal(target.getAttribute("data-appel"),
                     (ligne && ligne.querySelector(".row-name") || {}).textContent,
                     memoire.note, memoire.relance_le);
      return;
    }
  });

  // Les raisons de masquage sont transmises par la page de recherche
  document.addEventListener("DOMContentLoaded", function () {
    preparerAnimations();
    initEtatsDeSoumission();
    initTransitionsDePage();
    var holder = document.getElementById("raisons-masquage");
    if (holder) {
      try { window.MBDV_RAISONS = JSON.parse(holder.textContent); } catch (e) { /* ignore */ }
    }
  });

  document.addEventListener("submit", function (ev) {
    var formulaire = ev.target.closest ? ev.target.closest("form[data-form]") : null;
    if (!formulaire) return;
    ev.preventDefault();
    envoyerFormulaireSuivi(formulaire);
  });

  document.addEventListener("change", function (ev) {
    var select = ev.target.closest("[data-statut]");
    if (select) { actionStatut(select); return; }
    // Interrupteurs : le style suit :checked, on garde aussi la classe "on"
    // pour les navigateurs sans :has().
    var interrupteur = ev.target.closest(".toggle");
    if (interrupteur && ev.target.type === "checkbox") {
      interrupteur.classList.toggle("on", ev.target.checked);
    }
  });

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-filtre-suivi]");
    if (!btn) return;
    var filtre = btn.dataset.filtreSuivi;
    var cont = btn.closest(".repertoire-compteurs");
    if (cont) {
      var tousBtn = cont.querySelectorAll("[data-filtre-suivi]");
      for (var i = 0; i < tousBtn.length; i++) {
        tousBtn[i].classList.toggle("active", tousBtn[i] === btn);
      }
    }
    var rows = document.querySelectorAll("tbody tr[data-siren]");
    for (var j = 0; j < rows.length; j++) {
      var r = rows[j];
      var visible = true;
      if (filtre === "en_retard") visible = r.dataset.urgence === "en_retard";
      else if (filtre === "aujourd_hui") visible = r.dataset.urgence === "aujourd_hui";
      else if (filtre === "moi") visible = r.dataset.moi === "1";
      else if (filtre === "personne") visible = !r.dataset.prisPar;
      r.style.display = visible ? "" : "none";
    }
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      if (document.querySelector(".modal")) { closeHideModal(); return; }
      if (slideoverOuverte()) closeSlideover();
      return;
    }
    if (ev.key === "/" && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
      var actif = document.activeElement;
      var saisie = actif && (actif.tagName === "INPUT" || actif.tagName === "TEXTAREA" || actif.tagName === "SELECT");
      if (saisie) return;
      var q = document.getElementById("f-q");
      if (q) { ev.preventDefault(); q.focus(); q.select(); }
    }
  });
})();
