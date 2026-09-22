/* MBDV - Prospection : interactions (panneau fiche, masquage, suivi, toasts) */
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
    if (row && row.dataset.snapshot) return JSON.parse(row.dataset.snapshot);
    var over = document.querySelector(".slideover");
    if (over && over.dataset.snapshot) return JSON.parse(over.dataset.snapshot);
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
  document.addEventListener("click", function (ev) {
    var target = ev.target.closest("[data-detail],[data-hide],[data-follow],[data-recheck],[data-override],[data-restore],[data-close-slideover],[data-close-modal]");
    if (!target) return;

    if (target.hasAttribute("data-close-slideover") || target.hasAttribute("data-close-modal")) {
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
  });

  // Les raisons de masquage sont transmises par la page de recherche
  document.addEventListener("DOMContentLoaded", function () {
    preparerAnimations();
    initEtatsDeSoumission();
    var holder = document.getElementById("raisons-masquage");
    if (holder) {
      try { window.MBDV_RAISONS = JSON.parse(holder.textContent); } catch (e) { /* ignore */ }
    }
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
