// FairShare: small helpers. No inline scripts are used (the page's security policy forbids them).
document.addEventListener("DOMContentLoaded", function () {
  // 1) Ask before dangerous actions: <form data-confirm="Are you sure?">
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    });
  });

  // 2) Stop double submissions: after the first submit, disable the submit buttons.
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (event.defaultPrevented) { return; }
      form.querySelectorAll("button[type=submit]").forEach(function (btn) {
        window.setTimeout(function () { btn.disabled = true; }, 0);
      });
    });
  });

  // 3) Expense form: show the equal or custom split section and total up custom shares.
  var expenseForm = document.getElementById("expense-form");
  if (expenseForm) {
    var sections = expenseForm.querySelectorAll("[data-mode]");
    var radios = expenseForm.querySelectorAll("input[name=split_mode]");
    var show = function () {
      var mode = expenseForm.querySelector("input[name=split_mode]:checked").value;
      sections.forEach(function (s) { s.classList.toggle("d-none", s.getAttribute("data-mode") !== mode); });
    };
    radios.forEach(function (r) { r.addEventListener("change", show); });
    show();

    var totalBox = document.getElementById("share-total");
    var sumShares = function () {
      var cents = 0;
      expenseForm.querySelectorAll(".custom-share").forEach(function (input) {
        var v = parseFloat((input.value || "0").replace(/,/g, ""));
        if (!isNaN(v)) { cents += Math.round(v * 100); }
      });
      if (totalBox) { totalBox.textContent = (cents / 100).toFixed(2); }
    };
    expenseForm.querySelectorAll(".custom-share").forEach(function (i) { i.addEventListener("input", sumShares); });
    sumShares();
  }
});
