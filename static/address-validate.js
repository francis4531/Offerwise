/* OfferWise — shared strict US-address validator.
 *
 * ONE rule, enforced identically on every address entry point: the homepage
 * hero "Check Property" widget, the nav-bar "Check" widget, and the /risk-check
 * page. A complete address = street + city + 2-letter state + 5-digit ZIP.
 *
 * Returns { ok: boolean, message: string }. On ok === false, `message` is a
 * specific, user-facing sentence telling them exactly what is missing.
 */
(function (w) {
  function validate(raw) {
    var a = (raw || '').trim();
    if (a.length < 10) {
      return { ok: false, message: 'Please enter a full property address (e.g. 742 Oak Street, Austin, TX 78701).' };
    }
    // Require a city/state separator (comma) and a 2-letter state code.
    if (a.indexOf(',') === -1 || !/\b[A-Z]{2}\b/i.test(a)) {
      return { ok: false, message: 'Please include the city and state (e.g. Austin, TX 78701).' };
    }
    // Require a 5-digit ZIP — the backend needs one to resolve the property.
    if (!/\b\d{5}\b/.test(a)) {
      return { ok: false, message: 'Please include the 5-digit ZIP code for accurate results.' };
    }
    return { ok: true, message: '' };
  }
  w.OWAddress = { validate: validate };
})(window);
