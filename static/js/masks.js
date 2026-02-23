document.addEventListener('DOMContentLoaded', function() {
    
    // Função genérica para aplicar máscara
    function maskInput(input, format) {
        input.addEventListener('input', function(e) {
            let value = e.target.value.replace(/\D/g, ""); // Remove tudo que não é dígito
            let formattedValue = "";
            let valueIndex = 0;

            for (let i = 0; i < format.length; i++) {
                if (valueIndex >= value.length) break;

                if (format[i] === '#') {
                    formattedValue += value[valueIndex];
                    valueIndex++;
                } else {
                    formattedValue += format[i];
                }
            }
            e.target.value = formattedValue;
        });
    }

    // 1. Máscara CPF: 000.000.000-00
    const cpfInput = document.getElementById('id_cpf');
    if (cpfInput) {
        cpfInput.addEventListener('input', function(e) {
            let value = e.target.value.replace(/\D/g, "");
            if (value.length > 11) value = value.slice(0, 11);
            
            value = value.replace(/(\d{3})(\d)/, "$1.$2");
            value = value.replace(/(\d{3})(\d)/, "$1.$2");
            value = value.replace(/(\d{3})(\d{1,2})$/, "$1-$2");
            
            e.target.value = value;
        });
    }

    // 2. Máscara Telefone: (xx) x xxxx-xxxx
    const phoneInput = document.getElementById('id_telefone');
    if (phoneInput) {
        phoneInput.addEventListener('input', function(e) {
            let value = e.target.value.replace(/\D/g, "");
            if (value.length > 11) value = value.slice(0, 11);

            value = value.replace(/^(\d{2})(\d)/g, "($1) $2");
            value = value.replace(/(\d)(\d{4})$/, "$1-$2");
            
            e.target.value = value;
        });
    }

    // 3. Máscara CEP: xxxxx-xxx
    const cepInput = document.getElementById('id_cep');
    if (cepInput) {
        cepInput.addEventListener('input', function(e) {
            let value = e.target.value.replace(/\D/g, "");
            if (value.length > 8) value = value.slice(0, 8);
            
            value = value.replace(/^(\d{5})(\d)/, "$1-$2");
            
            e.target.value = value;
        });
    }
});