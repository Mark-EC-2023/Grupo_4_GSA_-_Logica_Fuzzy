# Grupo_4_GSA_-_Logica_Fuzzy
Repositório destinado para a organização das 200 simulações estocásticas LHS e refinamento das funções u(x).

Relatório Instrumentalização grupo 4

Nome equipe: Juan, Monaliza, Gabriel Braga, Marcos

Assuntos - 

## Planejamento

O objetivo do trabalho é transformar dados de temperatura e umidade em uma
decisão de controle adequada para um microcontrolador. A proposta planeja:

1. Uma base de dados gerada por simulações do virtual do Grupo 2
2. amostragem por Hipercubo Latino para cobrir o espaço de entradas com poucas
   simulações
3. funções de pertinência para representar conceitos linguísticos, como
   temperatura fria, morna ou quente para melhor "aproximação"
4. inferência fuzzy para calcular o nível de atuação do sistema de
   climatização e defuzzificação por centroide para obter um sinal numérico entre 0% e 100%.

O processo é planejado para a situação de como o microcontrolador deva se comportar com a entrada bruta de dados e como deverá agir sabendo que requitos indefinidos e subjetivos como  o que se considera "confontável". Onde com esse dado de entrada passando por uma fuzzificação para deixar-lo com níveis de intenciadade de uma característica e com uso das regras fuzzy, encontrar o valor satisfatório dentro das regras, onde poderá ser representado para o microcontrolador como um valor de intensidade linear. 

```text
Sensor -> filtragem -> fuzzificação -> regras fuzzy -> agregação -> centroide -> MQTT
'''

## Desenvolvimento

A tabela de saída usada diretamente no protótipo fuzzy está em
[`banco_dados_lhs_200.csv`](../Projeto/Data/banco_dados_lhs_200.csv) e contém
200 pares de:

- `LHS_Temperatura_C`;
- `LHS_Umidade_%`.

As amostras dessa tabela foram geradas com `scipy.stats.qmc.LatinHypercube`,
com duas variáveis e semente 42, escaladas para os limites físicos obtidos da
simulação-base. Portanto, elas são adequadas para testar o controlador em
diversas combinações de temperatura e umidade.

### 3.1 Geração do Hipercubo Latino

O LHS divide o intervalo de cada variável em estratos e seleciona uma amostra
de cada estrato, combinando as dimensões de forma aleatória. Diferentemente de
sortear todos os pontos livremente, essa estratégia reduz a concentração de
amostras em uma mesma região e permite explorar o espaço de entrada com 200
simulações.

No projeto, o notebook
[`Teste_Fuzzy.ipynb`](../Projeto/Teste_Fuzzy.ipynb) registra duas etapas:

1. geração da matriz LHS com os parâmetros de simulação
   (`temp_setpoint`, `potencia_equip`, `taxa_infiltracao` e
   `n_ocupantes`);
2. geração de 200 pares de temperatura e umidade a partir dos limites da simulação-base.


### 3.2 Fuzzificação

Para a temperatura, foram definidos os conjuntos:

- **Fria:** triangular `[15, 15, 25]`;
- **Morna:** triangular `[20, 26, 32]`;
- **Quente:** trapezoidal `[28, 35, 45, 45]`.

Para a umidade relativa:

- **Seca:** triangular `[0, 0, 40]`;
- **Ideal:** triangular `[30, 50, 70]`;
- **Úmida:** trapezoidal `[60, 80, 100, 100]`.

Cada leitura numérica é convertida em graus de pertinência entre 0 e 1. A
implementação atual usa funções triangulares e trapezoidais próprias, o que
facilita portar o cálculo para C/C++.

### 3.3 Inferência e defuzzificação

O protótipo usa composição de Zadeh:

- operador **E**: mínimo entre os graus de pertinência;
- agregação das regras: máximo;
- saída: nível de atuação do ar-condicionado de 0% a 100%;
- defuzzificação: centroide.

Uma base inicial de regras é:

| Regra | Condição | Saída |
|---|---|---|
| 1 | Se temperatura é fria **e** umidade é ideal | atuação fraca |
| 2 | Se temperatura é morna **e** umidade é úmida | atuação média |
| 3 | Se temperatura é quente **e** umidade é seca | atuação forte |

Essa base é um ponto de partida. Ela deve ser ampliada após ensaios, pois
combinações como “quente e úmida” também precisam produzir uma resposta
coerente de conforto e controle.

O centroide é calculado por:

```text
saida = soma(nivel * pertinencia_agregada(nivel))
        / soma(pertinencia_agregada(nivel))
```

Se nenhuma regra for ativada, o algoritmo deve tratar o denominador nulo e
usar uma saída segura previamente definida, em vez de executar uma divisão por
zero.

### 3.4 Algoritmo previsto para o microcontrolador

```text
inicializar_sensor()
inicializar_saida_pwm()

repetir a cada periodo:
    temperatura, umidade = ler_sensor()

    se leitura_invalida(temperatura, umidade):
        aplicar_saida_segura()
        continuar

    t_frio, t_morno, t_quente = fuzzificar_temperatura(temperatura)
    u_seca, u_ideal, u_umida = fuzzificar_umidade(umidade)

    r1 = minimo(t_frio,   u_ideal)
    r2 = minimo(t_morno,  u_umida)
    r3 = minimo(t_quente, u_seca)

    saida = centroide(r1, r2, r3)
    saida = limitar(saida, 0, 100)
    escrever_pwm(saida)
```

Para reduzir oscilações do atuador, recomenda-se acrescentar histerese ou
limitar a variação máxima da saída entre dois ciclos. A frequência de leitura,
o período de acionamento e os limites de segurança serão definidos conforme o
sensor e o atuador escolhidos.

## 4. Problemas encontrados

1. **Diferença entre as bases:** a matriz LHS de quatro parâmetros representa
   entradas da simulação, enquanto o banco LHS de duas colunas representa
   temperatura e umidade. É necessário documentar claramente se a primeira
   será usada apenas para gerar cenários ou se suas saídas serão incorporadas
   ao treinamento/validação.
2. **Protótipo ainda não embarcado:** o notebook depende de Python, NumPy,
   SciPy, Matplotlib e scikit-fuzzy. Essas dependências não devem ser levadas
   diretamente para o microcontrolador.
3. **Regras incompletas:** a implementação registrada contém três regras
   iniciais e não cobre todas as nove combinações entre os três conjuntos de
   temperatura e os três de umidade.
4. **Validação física pendente:** ainda não há, no material atual, identificação
   definitiva do sensor, da placa, do atuador nem ensaios comparando o comando
   fuzzy com uma referência de conforto.
5. **Tratamento de falhas:** leituras fora da faixa, falhas de comunicação e
   divisão por zero precisam ser tratados explicitamente no código embarcado.
6. **Oscilação do controle:** pequenas variações na leitura podem provocar
   mudanças frequentes no PWM; filtragem e histerese devem ser avaliadas.

## 5. O que faremos?

1. Consolidar a origem, unidade e finalidade de cada coluna das duas bases.
2. Completar a tabela de regras fuzzy e revisar os limiares com o grupo e com
   os dados de conforto.
3. Criar testes automatizados para pontos frios, confortáveis, quentes,
   secos, ideais, úmidos e para entradas fora da faixa.
4. Comparar a saída do notebook com uma implementação independente em C/C++,
   verificando tolerância numérica do centroide.
5. Escolher o microcontrolador, o sensor e o atuador; definir o período de
   amostragem e a saída elétrica segura.
6. Portar as funções de pertinência, regras e centroide para o firmware.
7. Testar primeiro com valores simulados e depois com o sensor real, registrando
   temperatura, umidade, saída fuzzy e estado do atuador.
8. Ajustar os parâmetros após os ensaios e documentar os resultados finais,
   incluindo limitações e possíveis trabalhos futuros.

## Conclusão

O projeto já possui uma base de 200 amostras LHS e um protótipo funcional de
fuzzificação, inferência e defuzzificação por centroide. A próxima etapa não é
gerar uma nova metodologia, mas consolidar as duas bases, completar as regras e
validar a equivalência entre o protótipo Python e o algoritmo executado no
microcontrolador. Com essa validação, a leitura contínua de temperatura e
umidade poderá ser convertida em um comando de controle simples, interpretável
e adequado a recursos embarcados.