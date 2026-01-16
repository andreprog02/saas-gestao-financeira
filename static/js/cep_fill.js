function onlyDigits(s){ return (s || "").replace(/\D/g, ""); }

document.addEventListener("DOMContentLoaded", () => {
  const cep = document.getElementById("id_cep");
  const logradouro = document.getElementById("id_logradouro");
  const bairro = document.getElementById("id_bairro");
  const cidade = document.getElementById("id_cidade");
  const uf = document.getElementById("id_uf");

  if (!cep) return;

  cep.addEventListener("blur", async () => {
    const digits = onlyDigits(cep.value);
    if (digits.length !== 8) return;

    try{
      const r = await fetch(`https://viacep.com.br/ws/${digits}/json/`);
      const data = await r.json();
      if (data.erro) return;

      if (logradouro) logradouro.value = data.logradouro || "";
      if (bairro) bairro.value = data.bairro || "";
      if (cidade) cidade.value = data.localidade || "";
      if (uf) uf.value = data.uf || "";
    }catch(e){
      // ignore
    }
  });
});
