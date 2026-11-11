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
2. geração de 200 pares de temperatura e umidade a partir dos limites da
   simulação-base.

	Desenvolvimento

	Problemas

	O que faremos?


