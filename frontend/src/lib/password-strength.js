// Shared password strength analyzer. Mirror of backend COMMON_PASSWORDS + rules.
export const COMMON_PASSWORDS = new Set([
  "123456","123456789","12345678","12345","1234567","1234567890",
  "password","password1","password123","qwerty","qwerty123","qwertyuiop",
  "abc123","111111","123123","000000","iloveyou","admin","admin123",
  "administrator","letmein","welcome","welcome1","monkey","dragon",
  "master","sunshine","princess","football","baseball","superman",
  "batman","trustno1","starwars","passw0rd","1q2w3e4r","1qaz2wsx",
  "zaq12wsx","qazwsx","asdfgh","asdfghjkl","qwerty1","qwertyu",
  "pokemon","hello","hello123","hello1","charlie","whatever",
  "shadow","ashley","michael","jennifer","thomas","jordan","jessica",
  "robert","daniel","andrew","joshua","matthew","nicole","amanda",
  "taylor","hunter","buster","soccer","hockey","killer","george",
  "sexy","andrea","michelle","love","login","test","test123",
  "guest","user","root","toor","changeme","qwer1234","qwer123",
  "p@ssw0rd","p@ssword","pa55word","pass123","pass1234","pass12345",
  "starqistna","starqistna123","bus123","ticket123",
  "malaysia","malaysia123","kuala","singapore",
]);

export function analyzePassword(pw) {
  const checks = {
    length: pw.length >= 8,
    upper: /[A-Z]/.test(pw),
    lower: /[a-z]/.test(pw),
    digit: /\d/.test(pw),
    notCommon: pw.length > 0 && !COMMON_PASSWORDS.has(pw.toLowerCase()),
  };
  const passed = Object.values(checks).filter(Boolean).length;
  let score = passed;
  if (pw.length >= 12 && passed === 5) score = 5;
  const label =
    pw.length === 0 ? "" :
    score <= 2 ? "Weak" :
    score === 3 ? "Fair" :
    score === 4 ? "Good" : "Strong";
  const color =
    score <= 2 ? "bg-red-500" :
    score === 3 ? "bg-amber-500" :
    score === 4 ? "bg-emerald-500" : "bg-emerald-600";
  return { checks, score, label, color, allPassed: passed === 5 };
}
