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
    // Require a 5-digit ZIP in the ZIP position — at the end of the address, or
    // in "ST 12345" form. This prevents a 5-digit STREET NUMBER (e.g. the "22580"
    // in "22580 San Vicente Avenue") from being mistaken for a ZIP and sent to the
    // geocoder, which then fails to resolve and surfaces a confusing error.
    var hasZip = /\b\d{5}(-\d{4})?\s*$/.test(a) || /\b[A-Z]{2}\s+\d{5}(-\d{4})?\b/i.test(a);
    if (!hasZip) {
      return { ok: false, message: 'Please include the 5-digit ZIP code for accurate results.' };
    }
    return { ok: true, message: '' };
  }
  w.OWAddress = { validate: validate };
})(window);
