"""
Verifica o GitHub Project da organizacao e comenta nas issues que estao em
"In Progress" ou "Test" com o Target date vencido, marcando os assignees.
Cada comentario e numerado: primeiro aviso, segundo aviso, terceiro aviso...
Sem dependencias externas: usa apenas a biblioteca padrao do Python.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, date
from zoneinfo import ZoneInfo

TOKEN = os.environ["GH_TOKEN"]  # token com acesso ao Project da organizacao
# Token do proprio workflow: comenta como github-actions[bot] neste repositorio
COMMENT_TOKEN = os.environ.get("COMMENT_TOKEN") or TOKEN
REPO_ATUAL = os.environ.get("GITHUB_REPOSITORY", "")
ORG = os.environ["ORG"]
PROJECT_NUMBER = int(os.environ["PROJECT_NUMBER"])
STATUS_FIELD = os.environ.get("STATUS_FIELD", "Status")
STATUS_ALVO = {s.strip().lower() for s in os.environ["STATUS_ALVO"].split(",")}
DATE_FIELD = os.environ.get("DATE_FIELD", "Target date")
INTERVALO_DIAS = int(os.environ.get("INTERVALO_DIAS", "2"))  # novo aviso a cada N dias
# Modo teste: numero de uma issue para comentar agora, ignorando o intervalo
ISSUE_TESTE = (os.environ.get("ISSUE_TESTE") or "").strip()

MARCADOR = "<!-- alerta-atraso-bot -->"
HOJE = datetime.now(ZoneInfo("America/Sao_Paulo")).date()

ORDINAIS = ["Primeiro", "Segundo", "Terceiro", "Quarto", "Quinto",
            "Sexto", "Sétimo", "Oitavo", "Nono", "Décimo"]

QUERY = """
query($org: String!, $number: Int!, $cursor: String, $status: String!, $data: String!) {
  organization(login: $org) {
    projectV2(number: $number) {
      title
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          status: fieldValueByName(name: $status) {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          prazo: fieldValueByName(name: $data) {
            ... on ProjectV2ItemFieldDateValue { date }
          }
          content {
            ... on Issue {
              number
              title
              url
              state
              repository { nameWithOwner }
              assignees(first: 20) { nodes { login } }
              issueFieldValues(first: 20) {
                nodes {
                  ... on IssueFieldDateValue {
                    value
                    field { ... on IssueFieldDate { name } }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def api(url, token, metodo="GET", corpo=None, extra_headers=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    headers.update(extra_headers or {})
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=dados, headers=headers, method=metodo)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def graphql(variables):
    payload = api("https://api.github.com/graphql", TOKEN, "POST",
                  {"query": QUERY, "variables": variables},
                  # Necessario para ler os campos da issue (Start date, Target date...)
                  {"GraphQL-Features": "issue_fields"})
    if payload.get("errors"):
        sys.exit(f"Erro na API do GitHub: {payload['errors']}")
    return payload["data"]["organization"]["projectV2"]


def buscar_itens():
    cursor, itens = None, []
    while True:
        projeto = graphql({"org": ORG, "number": PROJECT_NUMBER, "cursor": cursor,
                           "status": STATUS_FIELD, "data": DATE_FIELD})
        pagina = projeto["items"]
        itens.extend(pagina["nodes"])
        if not pagina["pageInfo"]["hasNextPage"]:
            return projeto["title"], itens
        cursor = pagina["pageInfo"]["endCursor"]


def token_para(repo):
    # O GITHUB_TOKEN so vale para o repositorio do workflow
    return COMMENT_TOKEN if repo == REPO_ATUAL else TOKEN


def avisos_anteriores(repo, numero):
    """Retorna a lista de datas dos avisos ja publicados pelo bot na issue."""
    datas, pagina = [], 1
    while True:
        url = (f"https://api.github.com/repos/{repo}/issues/{numero}/comments"
               f"?per_page=100&page={pagina}")
        comentarios = api(url, token_para(repo))
        for c in comentarios:
            if MARCADOR in (c.get("body") or ""):
                criado = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
                datas.append(criado.astimezone(ZoneInfo("America/Sao_Paulo")).date())
        if len(comentarios) < 100:
            return datas
        pagina += 1


def ordinal(n):
    return ORDINAIS[n - 1] if n <= len(ORDINAIS) else f"{n}º"


def montar_mensagem(n_aviso, assignees, status, prazo, dias, projeto):
    plural = "dia" if dias == 1 else "dias"
    if assignees:
        mencoes = " ".join(f"@{login}" for login in assignees)
        abertura = f"{mencoes}, esta issue está com o prazo vencido."
    else:
        abertura = ("Esta issue está com o prazo vencido e **não tem responsável atribuído**. "
                    "Atribua um assignee para que ele seja avisado.")
    return (
        f"{MARCADOR}\n"
        f"## {ordinal(n_aviso)} aviso de atraso\n\n"
        f"{abertura}\n\n"
        f"| Status atual | Target date | Atraso |\n|---|---|---|\n"
        f"| {status} | {prazo.strftime('%d/%m/%Y')} | {dias} {plural} |\n\n"
        f"Combine um novo prazo de entrega com o seu orientador, **Arthur Mariano**, "
        f"e com a sua equipe, ou conclua esta issue o quanto antes. Lembre-se: "
        f"**a data não deve ser alterada sem a aprovação prévia do seu orientador.**\n\n"
        "_Irei emitir esta mensagem periodicamente até que a situação desta issue seja resolvida._"
    )


def main():
    projeto, itens = buscar_itens()
    publicados = 0

    for item in itens:
        issue = item.get("content") or {}
        status = (item.get("status") or {}).get("name")
        if not issue.get("number") or issue.get("state") != "OPEN":
            continue
        if ISSUE_TESTE and str(issue["number"]) != ISSUE_TESTE:
            continue
        # Prazo: campo do Project; se nao existir, usa o campo da propria issue
        prazo_txt = (item.get("prazo") or {}).get("date")
        if not prazo_txt:
            for valor in (issue.get("issueFieldValues") or {}).get("nodes") or []:
                if (valor.get("field") or {}).get("name", "").lower() == DATE_FIELD.lower():
                    prazo_txt = (valor.get("value") or "")[:10]
        if not status or status.lower() not in STATUS_ALVO or not prazo_txt:
            if ISSUE_TESTE:
                print(f"Teste: #{ISSUE_TESTE} ignorada (status={status}, prazo={prazo_txt})")
            continue
        prazo = date.fromisoformat(prazo_txt)
        if prazo >= HOJE:
            if ISSUE_TESTE:
                print(f"Teste: #{ISSUE_TESTE} ainda no prazo ({prazo_txt})")
            continue

        repo = issue["repository"]["nameWithOwner"]
        numero = issue["number"]
        dias = (HOJE - prazo).days
        anteriores = avisos_anteriores(repo, numero)

        # Primeiro aviso logo no 1o dia de atraso; depois, a cada INTERVALO_DIAS
        if anteriores and not ISSUE_TESTE:
            desde_ultimo = (HOJE - max(anteriores)).days
            if desde_ultimo < INTERVALO_DIAS:
                print(f"#{numero} atrasada ({dias} dias), ultimo aviso ha {desde_ultimo} dia(s): sem aviso hoje")
                continue

        n_aviso = len(anteriores) + 1
        assignees = [p["login"] for p in issue["assignees"]["nodes"]]
        corpo = montar_mensagem(n_aviso, assignees, status, prazo, dias, projeto)
        api(f"https://api.github.com/repos/{repo}/issues/{numero}/comments",
            token_para(repo), "POST", {"body": corpo})
        publicados += 1
        print(f"#{numero} {issue['title']}: {ordinal(n_aviso).lower()} aviso publicado "
              f"({dias} dias de atraso, assignees: {', '.join(assignees) or 'nenhum'})")

    print(f"\nTotal de avisos publicados: {publicados}")


if __name__ == "__main__":
    main()
