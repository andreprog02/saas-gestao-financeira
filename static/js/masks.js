document.addEventListener('DOMContentLoaded', function() {
    
    // ==========================================================
    // MÁSCARA MONETÁRIA (ESTILO CAIXA ELETRÔNICO / ATM)
    // Digita: 1234 -> Vira: R$ 12,34
    // ==========================================================
    const applyMoneyMask = (input) => {
        let value = input.value.replace(/\D/g, ""); // Remove tudo que não for número
        
        if (value === "") {
            input.value = "";
            return;
        }

        // Converte para centavos (divide por 100)
        value = (parseInt(value) / 100).toFixed(2);
        
        // Separa parte inteira e decimal
        let parts = value.split('.');
        
        // Adiciona separador de milhar (.)
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ".");
        
        // Retorna formatado: R$ 1.000,00
        input.value = "R$ " + parts.join(",");
    };

    // Inicializa todos os campos com a classe 'money-mask'
    const moneyInputs = document.querySelectorAll('.money-mask');
    moneyInputs.forEach(input => {
        // Formata valor inicial se houver (ex: vindo do banco ao editar)
        if (input.value && !input.value.includes('R$')) {
            // Garante que o valor seja tratado como centavos
            // Ex: 1500.00 -> 150000 -> R$ 1.500,00
            let rawValue = parseFloat(input.value).toFixed(2).replace('.', '');
            input.value = rawValue;
            applyMoneyMask(input);
        }

        input.addEventListener('input', () => applyMoneyMask(input));
    });
});