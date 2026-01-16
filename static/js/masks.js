function onlyDigits(s){ return (s || "").replace(/\D/g, ""); }

function maskCPF(el){
  let v = onlyDigits(el.value).slice(0,11);
  v = v.replace(/(\d{3})(\d)/, "$1.$2");
  v = v.replace(/(\d{3})(\d)/, "$1.$2");
  v = v.replace(/(\d{3})(\d{1,2})$/, "$1-$2");
  el.value = v;
}

function maskCEP(el){
  let v = onlyDigits(el.value).slice(0,8);
  if (v.length > 5) v = v.replace(/(\d{5})(\d)/, "$1-$2");
  el.value = v;
}

function maskTelefone(el){
  let v = onlyDigits(el.value).slice(0,11);
  if (v.length < 2){ el.value = v; return; }
  const ddd = v.slice(0,2);
  const dig = v.slice(2,3);
  const mid = v.slice(3,7);
  const end = v.slice(7,11);
  let out = `(${ddd})`;
  if (dig) out += ` ${dig}`;
  if (mid) out += ` ${mid}`;
  if (end) out += `-${end}`;
  el.value = out;
}

document.addEventListener("DOMContentLoaded", () => {
  const cpf = document.getElementById("id_cpf");
  const cep = document.getElementById("id_cep");
  const tel = document.getElementById("id_telefone");

  if (cpf) cpf.addEventListener("input", () => maskCPF(cpf));
  if (cep) cep.addEventListener("input", () => maskCEP(cep));
  if (tel) tel.addEventListener("input", () => maskTelefone(tel));
});
